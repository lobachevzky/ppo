import numpy as np
import torch
import torch.nn.functional as F
from gym import spaces
from torch import nn as nn

from ppo.utils import init_


class Recurrence:
    def __init__(self, conv_hidden_size, num_conv_layers):
        self.conv_hidden_size = conv_hidden_size
        d = self.obs_spaces.obs.shape[0]
        layers = []
        if num_conv_layers <= 0:
            self.conv = nn.Sequential(
                nn.Conv2d(d, conv_hidden_size, kernel_size=3),
                nn.ReLU(),
                nn.Conv2d(conv_hidden_size, conv_hidden_size, kernel_size=4),
                nn.ReLU(),
            )
        else:
            in_size = d
            for _ in range(num_conv_layers):
                layers += [
                    nn.Conv2d(in_size, conv_hidden_size, kernel_size=1),
                    nn.ReLU(),
                ]
                in_size = conv_hidden_size
            self.conv = nn.Sequential(
                *layers, nn.AvgPool2d(kernel_size=self.obs_spaces.obs.shape[1:])
            )
        ones = torch.ones(1, dtype=torch.long)
        self.register_buffer("ones", ones)
        line_nvec = torch.tensor(self.obs_spaces.lines.nvec[0, :-1])
        offset = F.pad(line_nvec.cumsum(0), [1, 0])
        self.register_buffer("offset", offset)

    @property
    def gru_in_size(self):
        return self.hidden_size + self.encoder_hidden_size

    @staticmethod
    def eval_lines_space(n_eval_lines, train_lines_space):
        return spaces.MultiDiscrete(
            np.repeat(train_lines_space.nvec[:1], repeats=n_eval_lines, axis=0)
        )

    def build_embed_task(self, hidden_size):
        return nn.EmbeddingBag(self.obs_spaces.lines.nvec[0].sum(), hidden_size)

    def build_memory(self, N, T, inputs):
        lines = inputs.lines.view(T, N, *self.obs_spaces.lines.shape)
        lines = lines.long()[0, :, :] + self.offset
        return self.embed_task(lines.view(-1, self.obs_spaces.lines.nvec[0].size)).view(
            *lines.shape[:2], self.encoder_hidden_size
        )  # n_batch, n_lines, hidden_size

    def preprocess_obs(self, obs):
        return obs.sum(-1).sum(-1).repeat(1, 2)

        # N = obs.size(0)
        # return self.conv(obs).view(N, -1)
