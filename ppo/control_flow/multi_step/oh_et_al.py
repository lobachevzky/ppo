from collections import namedtuple

import torch
from torch import nn as nn

import ppo.control_flow.multi_step.abstract_recurrence as abstract_recurrence
import ppo.control_flow.oh_et_al as oh_et_al
from ppo.distributions import FixedCategorical
from ppo.utils import init_
import numpy as np

RecurrentState = namedtuple("RecurrentState", "a v h h2 p ag dg a_probs")


def gate(g, new, old):
    return FixedCategorical(probs=g * new + (1 - g) * old)


class Recurrence(abstract_recurrence.Recurrence, oh_et_al.Recurrence):
    def __init__(self, hidden_size, gate_coef, conv_hidden_size, use_conv, **kwargs):
        oh_et_al.Recurrence.__init__(
            self,
            hidden_size=hidden_size,
            use_conv=use_conv,
            conv_hidden_size=conv_hidden_size,
            **kwargs,
        )
        abstract_recurrence.Recurrence.__init__(
            self, conv_hidden_size=conv_hidden_size, use_conv=use_conv
        )
        self.gate_coef = gate_coef
        self.d_gate = nn.Sequential(init_(nn.Linear(2 * hidden_size, 1)), nn.Sigmoid())
        self.a_gate = nn.Sequential(init_(nn.Linear(2 * hidden_size, 1)), nn.Sigmoid())
        self.state_sizes = RecurrentState(**self.state_sizes._asdict(), ag=1, dg=1)

    def parse_hidden(self, hx: torch.Tensor) -> RecurrentState:
        return RecurrentState(*torch.split(hx, self.state_sizes, dim=-1))

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

    def inner_loop(self, inputs, rnn_hxs):
        T, N, dim = inputs.shape
        inputs, actions = torch.split(
            inputs.detach(), [dim - self.action_size, self.action_size], dim=2
        )

        # parse non-action inputs
        inputs = self.parse_inputs(inputs)
        inputs = inputs._replace(obs=inputs.obs.view(T, N, *self.obs_spaces.obs.shape))

        # build memory
        lines = inputs.lines.view(T, N, *self.obs_spaces.lines.shape)
        lines = lines.long()[0, :, :] + self.offset
        M = self.embed_task(lines.view(-1, self.obs_spaces.lines.nvec[0].size)).view(
            *lines.shape[:2], self.encoder_hidden_size
        )  # n_batch, n_lines, hidden_size

        new_episode = torch.all(rnn_hxs == 0, dim=-1).squeeze(0)
        hx = self.parse_hidden(rnn_hxs)
        for _x in hx:
            _x.squeeze_(0)

        h = hx.h
        h2 = hx.h2
        p = hx.p
        p[new_episode, 0] = 1
        hx.a[new_episode] = self.n_a - 1
        A = torch.cat([actions[:, :, 0], hx.a.view(1, N)], dim=0).long()

        for t in range(T):
            obs = self.preprocess_obs(inputs.obs[t])
            r = (p.unsqueeze(1) @ M).squeeze(1)
            x = [obs, r, self.embed_action(A[t - 1].clone())]
            h_cat = torch.cat([h, h2], dim=-1)
            h_cat2 = self.gru(torch.cat(x, dim=-1), h_cat)
            z = torch.relu(self.zeta(h_cat2))
            d_gate = self.d_gate(z)
            a_gate = self.a_gate(z)

            l = self.upsilon(z).softmax(dim=-1)
            p_ = oh_et_al.batch_conv1d(p, l)
            p = d_gate * p_ + (1 - d_gate) * p

            a_probs = self.actor(z).probs
            old = torch.zeros_like(a_probs).scatter(1, A[t - 1].unsqueeze(1), 1)
            a_dist = gate(a_gate, a_probs, old)
            self.sample_new(A[t], a_dist)
            # self.print("ag prob", torch.round(100 * a_gate.probs[:, 1]))

            h_size = self.hidden_size // 2
            h_, h2 = torch.split(h_cat2, [h_size, h_size], dim=-1)
            h = d_gate * h_ + (1 - d_gate) * h_

            yield (
                RecurrentState(
                    a=A[t],
                    v=self.critic(z),
                    h=h,
                    p=p,
                    h2=h2,
                    a_probs=a_dist.probs,
                    ag=a_gate,
                    dg=d_gate,
                )
            )