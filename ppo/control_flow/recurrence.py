from collections import namedtuple

import numpy as np
import torch
import torch.nn.functional as F
from torch import nn as nn

from ppo.distributions import FixedCategorical, Categorical
from ppo.control_flow.env import Obs
from ppo.layers import Concat
from ppo.utils import init_

RecurrentState = namedtuple("RecurrentState", "a p v h a_probs p_probs")


def batch_conv1d(inputs, weights):
    outputs = []
    # one convolution per instance
    n = inputs.shape[0]
    for i in range(n):
        x = inputs[i]
        w = weights[i]
        convolved = F.conv1d(x.reshape(1, 1, -1), w.reshape(1, 1, -1), padding=2)
        outputs.append(convolved.squeeze(0))
    padded = torch.cat(outputs)
    padded[:, 1] = padded[:, 1] + padded[:, 0]
    padded[:, -2] = padded[:, -2] + padded[:, -1]
    return padded[:, 1:-1]


class Recurrence(nn.Module):
    def __init__(
        self,
        observation_space,
        action_space,
        activation,
        hidden_size,
        num_layers,
        num_attention_layers,
        debug,
        baseline,
    ):
        super().__init__()
        self.baseline = baseline
        self.obs_spaces = Obs(**observation_space.spaces)
        self.obs_sections = Obs(*[int(np.prod(s.shape)) for s in self.obs_spaces])
        self.action_size = 2
        self.debug = debug
        self.hidden_size = hidden_size

        # networks
        nl = int(self.obs_spaces.lines.nvec[0])
        self.embeddings = nn.Embedding(nl, hidden_size)
        self.task_encoder = nn.GRU(hidden_size, hidden_size, bidirectional=True)

        # f
        # layers = [Concat(dim=-1)]
        # in_size = self.obs_sections.condition + (2 if baseline else 1) * hidden_size
        layers = []
        in_size = self.obs_sections.condition
        for _ in range(num_layers + 1):
            layers.extend([init_(nn.Linear(in_size, hidden_size)), activation])
            in_size = hidden_size
        self.f = nn.Sequential(*layers)

        self.na = na = int(action_space.nvec[0])
        self.gru = nn.GRUCell(hidden_size, hidden_size)

        layers = []
        in_size = int(sum(self.obs_sections))
        for _ in range(num_layers):
            layers.extend([nn.Linear(in_size, hidden_size), activation])
            in_size = hidden_size
        self.critic = nn.Sequential(*layers, init_(nn.Linear(in_size, 1)))

        self.action_embedding = nn.Embedding(na, hidden_size)
        layers = []
        for _ in range(num_attention_layers):
            layers.extend([nn.Linear(hidden_size, hidden_size), activation])
        self.attention = nn.Sequential(
            *layers, Categorical(in_size, self.obs_sections.lines)
        )
        # self.actor = Categorical(hidden_size, na)
        # self.no = 2
        # self.linear = init_(nn.Linear(hidden_size, self.no))
        # self.linear2 = init_(nn.Linear(1, self.no))
        # self.a_one_hots = nn.Embedding.from_pretrained(torch.eye(na))
        self.state_sizes = RecurrentState(
            a=1, a_probs=na, p=1, p_probs=self.obs_sections.lines, v=1, h=hidden_size
        )

    @staticmethod
    def sample_new(x, dist):
        new = x < 0
        x[new] = dist.sample()[new].flatten()

    def forward(self, inputs, hx):
        return self.pack(self.inner_loop(inputs, rnn_hxs=hx))

    def pack(self, hxs):
        def pack():
            for name, size, hx in zip(
                RecurrentState._fields, self.state_sizes, zip(*hxs)
            ):
                x = torch.stack(hx).float()
                assert np.prod(x.shape[2:]) == size
                yield x.view(*x.shape[:2], -1)

        hx = torch.cat(list(pack()), dim=-1)
        return hx, hx[-1:]

    def parse_inputs(self, inputs: torch.Tensor):
        return Obs(*torch.split(inputs, self.obs_sections, dim=-1))

    def parse_hidden(self, hx: torch.Tensor) -> RecurrentState:
        return RecurrentState(*torch.split(hx, self.state_sizes, dim=-1))

    def print(self, *args, **kwargs):
        if self.debug:
            print(*args, **kwargs)

    def inner_loop(self, inputs, rnn_hxs):
        T, N, D = inputs.shape
        inputs, actions = torch.split(
            inputs.detach(), [D - self.action_size, self.action_size], dim=2
        )

        # parse non-action inputs
        all_inputs = inputs
        inputs = self.parse_inputs(inputs)

        # build memory
        lines = inputs.lines.view(T, N, *self.obs_spaces.lines.shape).long()[0, :, :]
        M = self.embeddings(lines.view(-1)).view(
            *lines.shape, self.hidden_size
        )  # n_batch, n_lines, hidden_size
        # forward_input = M.transpose(0, 1)  # n_lines, n_batch, hidden_size
        # K, Kn = self.task_encoder(forward_input)
        # Kn = Kn.transpose(0, 1).reshape(N, -1)
        # K = K.transpose(0, 1)

        # forward_input = M.transpose(0, 1)  # n_lines, n_batch, hidden_size
        # backward_input = forward_input.flip((0,))
        # keys = []
        # for i in range(len(forward_input)):
        #     keys_per_i = []
        #     if i > 0:
        #         backward, _ = self.task_encoder(backward_input[:i])
        #         keys_per_i.append(backward.flip((0,)))
        #     if i < len(forward_input):
        #         forward, _ = self.task_encoder(forward_input[i:])
        #         keys_per_i.append(forward)
        #     keys.append(torch.cat(keys_per_i).transpose(0, 1))  # put batch dim first
        # K = torch.stack(keys, dim=1)  # put from dim before to dim
        # K, C = torch.split(K, [self.hidden_size, 1], dim=-1)
        # K = K.sum(dim=1)
        # C = C.squeeze(dim=-1)
        gru_input = M.transpose(0, 1)

        K = []
        for i in range(self.obs_sections.lines):
            k, _ = self.task_encoder(torch.roll(gru_input, shifts=i, dims=0))
            K.append(k)
        S = torch.stack(K, dim=0)  # ns, ns, nb, 2*h
        V = S.view(S.size(0), S.size(1), N, 2, -1)  # ns, ns, nb, 2, h
        K0 = V.permute(2, 0, 1, 3, 4)  # nb, ns, ns, 2, h
        K = K0.reshape(N, K0.size(1), -1, K0.size(-1))

        new_episode = torch.all(rnn_hxs == 0, dim=-1).squeeze(0)
        hx = self.parse_hidden(rnn_hxs)
        for _x in hx:
            _x.squeeze_(0)

        h = hx.h
        a = hx.a.long().squeeze(-1)
        a[new_episode] = 0
        R = torch.arange(N, device=rnn_hxs.device)
        A = torch.cat([actions[:, :, 0], hx.a.view(1, N)], dim=0).long()
        P = torch.cat([actions[:, :, 1], hx.p.view(1, N)], dim=0).long()

        for t in range(T):
            # r = M[R, a]
            # if self.baseline:
            h = self.gru(self.action_embedding(A[t - 1].clone()), h)
            p_dist = self.attention(h)
            self.sample_new(P[t], p_dist)
            # else:
            #     h = self.gru(self.f((inputs.condition[t], r)), h)
            q = self.f(inputs.condition[t])
            k = K[R, P[t].clone()]
            l = torch.sum(k * q.unsqueeze(1), dim=-1)
            # w = F.softmax(self.linear2(inputs.condition[t]), dim=-1)

            # a_dist = self.actor(h)
            # q = self.linear(h)
            # k = (K @ q.unsqueeze(2)).squeeze(2)
            # self.print("k")
            # self.print(k)
            # p_dist = FixedCategorical(logits=k)
            # self.print("dist")
            # self.print(p_dist.probs)
            # o = O[R, inputs.active[t].long().squeeze(-1)]
            # h = self.gru(self.f((inputs.condition[t], r)), h)
            # ow = torch.sum(o * w.unsqueeze(1), dim=-1)
            a_dist = FixedCategorical(logits=l)
            self.sample_new(A[t], a_dist)
            # a = torch.clamp(a + P[t] - self.na, 0, self.na - 1)
            # a = a + A[t]
            # self.sample_new(A[t], a_dist
            yield RecurrentState(
                a=A[t],
                v=self.critic(all_inputs[t]),
                h=hx.h,
                a_probs=a_dist.probs,
                p=P[t],
                p_probs=p_dist.probs,
            )
