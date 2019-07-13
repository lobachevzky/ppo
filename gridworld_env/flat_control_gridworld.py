from collections import Counter, OrderedDict, namedtuple
from enum import Enum

from gym import spaces
import numpy as np

from dataclasses import dataclass
from gridworld_env import SubtasksGridWorld
from gridworld_env.control_flow_gridworld import ControlFlowGridWorld

Obs = namedtuple(
    "Obs", "base subtask subtasks conditions control next_subtask pred lines"
)


class Else:
    def __str__(self):
        return "else:"


class EndIf:
    def __str__(self):
        return "endif"


def filter_for_obs(d):
    return {k: v for k, v in d.items() if k in Obs._fields}


class FlatControlFlowGridWorld(ControlFlowGridWorld):
    def __init__(self, *args, n_subtasks, **kwargs):
        n_subtasks += 1
        SubtasksGridWorld.__init__(self, *args, n_subtasks=n_subtasks, **kwargs)
        self.passing_prob = 0.5
        self.pred = None
        self.force_branching = False

        self.conditions = None
        self.control = None
        self.required_objects = None
        self.observation_space.spaces.update(
            subtask=spaces.Discrete(self.observation_space.spaces["subtask"].n + 1),
            conditions=spaces.MultiDiscrete(
                np.array([len(self.object_types)]).repeat(self.n_subtasks)
            ),
            pred=spaces.Discrete(2),
            control=spaces.MultiDiscrete(
                np.tile(
                    np.array([[1 + self.n_subtasks]]),
                    [self.n_subtasks, 2],  # binary conditions
                )
            ),
        )
        obs_spaces = self.observation_space.spaces
        subtask_nvec = obs_spaces["subtasks"].nvec[0]
        self.lines = None
        self.required_objects = None
        # noinspection PyProtectedMember
        self.n_lines = self.n_subtasks + self.n_subtasks // 2 - 1
        self.observation_space.spaces.update(
            lines=spaces.MultiDiscrete(
                np.tile(
                    np.pad(
                        subtask_nvec,
                        [0, 1],
                        "constant",
                        constant_values=1 + len(self.object_types),
                    ),
                    (self.n_lines, 1),
                )
            )
        )
        self.observation_space.spaces = Obs(
            **filter_for_obs(self.observation_space.spaces)
        )._asdict()
        world = self

        @dataclass
        class If:
            obj: int

            def __str__(self):
                return f"if {world.object_types[self.obj]}:"

        self.If = If

    def task_string(self):
        lines = iter(self.lines)

        def helper():
            while True:
                line = next(lines, None)
                if line is None:
                    return
                if isinstance(line, self.Subtask):
                    yield str(line)
                elif isinstance(line, self.If):
                    yield str(line)
                    yield f"    {next(lines)}"

        return "\n".join(helper())

    def reset(self):
        one_step_episode = self.np_random.rand() < 0.5
        if one_step_episode:
            self.branching_episode = self.np_random.rand() < 0.5

            # agent has to take one step when either
            if self.branching_episode:
                # encontering a passing condition
                self.passing_prob = 1
            else:
                # or a subtask
                self.passing_prob = 0.5
        else:
            # agent has to take two steps when encountering a failed condition
            self.branching_episode = True
            self.passing_prob = 0
        o = super().reset()
        self.subtask_idx = self.get_next_subtask()
        return o

    def step(self, action):
        s, r, t, i = super().step(action)
        i.update(passing=self.passing)
        return s, r, t, i

    def get_observation(self):
        obs = super().get_observation()

        def get_lines():
            for subtask, (pos, neg), condition in zip(
                self.subtasks, self.control, self.conditions
            ):
                yield subtask + (0,)
                if pos != neg:
                    yield (0, 0, 0, condition + 1)

        lines = np.vstack(list(get_lines())[1:])
        self.lines = np.pad(lines, [(0, self.n_lines - len(lines)), (0, 0)], "constant")

        obs.update(lines=self.lines)
        for (k, s) in self.observation_space.spaces.items():
            assert s.contains(obs[k])
        return OrderedDict(filter_for_obs(obs))

    def get_control(self):
        for i in range(self.n_subtasks):
            if i % 3 == 0:
                if self.branching_episode:
                    yield i + 1, i
                else:
                    yield i, i
            elif i % 3 == 1:
                if self.branching_episode:
                    yield i + 2, i + 2  # terminate
                else:
                    yield i, i
            elif i % 3 == 2:
                yield i + 1, i + 1  # terminate

    def choose_subtasks(self):
        if not self.branching_episode:
            yield from super().choose_subtasks()
            return
        irreversible_interactions = [
            j for j, i in enumerate(self.interactions) if i in ("pick-up", "transform")
        ]
        passing, failing = irreversible_interactions
        conditional_object = self.np_random.choice(len(self.object_types))
        for i in range(self.n_subtasks):
            self.np_random.shuffle(irreversible_interactions)
            if i % 3 == 0:
                j = self.np_random.choice(len(self.possible_subtasks))
                yield self.Subtask(*self.possible_subtasks[j])
            if i % 3 == 1:
                yield self.Subtask(
                    interaction=passing, count=0, object=conditional_object
                )
            if i % 3 == 2:
                yield self.Subtask(
                    interaction=failing, count=0, object=conditional_object
                )


def main(seed, n_subtasks):
    kwargs = gridworld_env.get_args("4x4SubtasksGridWorld-v0")
    del kwargs["class_"]
    del kwargs["max_episode_steps"]
    kwargs.update(n_subtasks=n_subtasks, max_task_count=1)
    env = FlatControlFlowGridWorld(**kwargs, evaluation=False, eval_subtasks=[])
    actions = "wsadeq"
    gridworld_env.keyboard_control.run(env, actions=actions, seed=seed)


if __name__ == "__main__":
    import argparse
    import gridworld_env.keyboard_control

    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--n-subtasks", type=int, default=5)
    main(**vars(parser.parse_args()))
