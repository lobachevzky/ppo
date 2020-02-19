from collections import namedtuple

import torch
import torch.nn.functional as F
from torch import nn as nn

import ppo.bandit.bandit
from ppo.distributions import FixedCategorical, Categorical
from ppo.layers import Concat
from ppo.maze.env import Actions
from ppo.utils import init_

RecurrentState = namedtuple("RecurrentState", "a v h a_probs")


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
        debug,
    ):
        super().__init__()
        self.obs_shape = d, h, w = observation_space.shape
        self.action_size = 1
        self.debug = debug
        self.hidden_size = hidden_size

        # networks
        layers = []
        in_size = d
        for _ in range(num_layers + 1):
            layers += [
                nn.Conv2d(in_size, hidden_size, kernel_size=3, padding=1),
                activation,
            ]
            in_size = hidden_size
        self.task_embedding = nn.Sequential(*layers)
        self.task_encoder = nn.GRU(hidden_size, hidden_size, bidirectional=True)
        self.gru = nn.GRUCell(hidden_size, hidden_size)
        self.critic = init_(nn.Linear(h * w * hidden_size, 1))
        self.actor = init_(nn.Linear(hidden_size, 1))
        self.f = nn.Linear(hidden_size * 2, hidden_size)
        self.a_one_hots = nn.Embedding.from_pretrained(torch.eye(h * w))
        self.state_sizes = RecurrentState(a=1, a_probs=h * w * 2, v=1, h=hidden_size)

    @staticmethod
    def sample_new(x, dist):
        new = x < 0
        x[new] = dist.sample()[new].flatten()

    def forward(self, inputs, hx):
        return self.pack(self.inner_loop(inputs, rnn_hxs=hx))

    @staticmethod
    def pack(hxs):
        def pack():
            for name, hx in RecurrentState(*zip(*hxs))._asdict().items():
                x = torch.stack(hx).float()
                yield x.view(*x.shape[:2], -1)

        hx = torch.cat(list(pack()), dim=-1)
        return hx, hx[-1:]

    def parse_inputs(self, inputs: torch.Tensor):
        return ppo.bandit.bandit.Obs(*torch.split(inputs, self.obs_sections, dim=-1))

    def parse_hidden(self, hx: torch.Tensor) -> RecurrentState:
        return RecurrentState(*torch.split(hx, self.state_sizes, dim=-1))

    def print(self, *args, **kwargs):
        if self.debug:
            print(*args, **kwargs)

    def inner_loop(self, inputs, rnn_hxs):
        T, N, D = inputs.shape
        obs, actions = torch.split(
            inputs.detach(), [D - self.action_size, self.action_size], dim=-1
        )
        obs = obs[0]

        # build memory
        M = (
            self.task_embedding(obs.view(N, *self.obs_shape))  # N, hidden_size, h, w
            .view(N, self.hidden_size, -1)  # N, hidden_size, h * w
            .transpose(1, 2)  # N, h * w, hidden_size
        )
        K, _ = self.task_encoder(M.transpose(0, 1))  # h * w, N, hidden_size * 2
        K = K.transpose(0, 1)  # N, h * w, hidden_size * 2

        hx = self.parse_hidden(rnn_hxs)
        for _x in hx:
            _x.squeeze_(0)

        A = torch.cat([actions, hx.a.unsqueeze(0)], dim=0).long().squeeze(2)

        for t in range(T):
            h = self.f(K.reshape(-1, self.hidden_size * 2)).view(
                N, -1, self.hidden_size
            )  # N, h * w
            probs = F.softmax(self.actor(h).squeeze(-1), -1)
            probs = torch.cat([torch.zeros_like(probs), probs], dim=-1)
            dist = FixedCategorical(probs=probs)
            self.sample_new(A[t], dist)
            yield RecurrentState(
                a=A[t], v=self.critic(h.reshape(N, -1)), h=hx.h, a_probs=dist.probs
            )