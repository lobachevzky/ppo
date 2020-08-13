import abc
import itertools
import sys
import time
from collections import defaultdict, deque, namedtuple
from pathlib import Path
from pprint import pprint
from typing import Dict

import gym
import numpy as np
import torch
from ray import tune
from tensorboardX import SummaryWriter

from agent import Agent, AgentOutputs
from common.vec_env.dummy_vec_env import DummyVecEnv
from common.vec_env.subproc_vec_env import SubprocVecEnv
from common.vec_env.util import set_seeds
from ppo import PPO
from rollouts import RolloutStorage
from utils import k_scalar_pairs
from wrappers import VecPyTorch

EpochOutputs = namedtuple("EpochOutputs", "obs reward done infos act masks")


# noinspection PyAttributeOutsideInit
class Trainer(abc.ABC):
    def __init__(
        self,
        run_id,
        save_interval: int,
        num_steps,
        eval_steps,
        num_processes,
        seed,
        cuda_deterministic,
        cuda,
        gamma,
        normalize,
        log_interval,
        eval_interval,
        use_gae,
        tau,
        ppo_args,
        agent_args,
        render,
        render_eval,
        load_path,
        synchronous,
        num_batch,
        env_args,
        success_reward,
        use_tune=False,
        log_dir: Path = "dumb",
        no_eval=False,
    ):
        if not torch.cuda.is_available():
            cuda = False
        set_seeds(cuda=cuda, cuda_deterministic=cuda_deterministic, seed=seed)

        if log_dir:
            self.writer = SummaryWriter(logdir=str(log_dir))
        else:
            self.writer = None

        if render_eval and not render:
            eval_interval = 1
        if render or render_eval:
            ppo_args.update(ppo_epoch=0)
            num_processes = 1
            cuda = False

        device = torch.device("cuda" if cuda else "cpu")

        def make_vec_envs(evaluation):
            def env_thunk(rank):
                return self.make_env(
                    seed=seed, rank=rank, evaluation=evaluation, **env_args
                )

            env_fns = [lambda: env_thunk(i) for i in range(num_processes)]
            use_dummy = len(env_fns) == 1 or sys.platform == "darwin" or synchronous
            return VecPyTorch(
                DummyVecEnv(env_fns, render=render)
                if use_dummy
                else SubprocVecEnv(env_fns)
            )

        train_envs = make_vec_envs(evaluation=False)

        train_envs.to(device)
        self.agent = agent = self.build_agent(envs=train_envs, **agent_args)
        rollouts = RolloutStorage(
            num_steps=num_steps,
            num_processes=num_processes,
            obs_space=train_envs.observation_space,
            action_space=train_envs.action_space,
            recurrent_hidden_state_size=agent.recurrent_hidden_state_size,
            use_gae=use_gae,
            gamma=gamma,
            tau=tau,
        )

        # copy to device
        if cuda:
            tick = time.time()
            agent.to(device)
            rollouts.to(device)
            print("Values copied to GPU in", time.time() - tick, "seconds")

        ppo = PPO(agent=agent, num_batch=num_batch, **ppo_args)

        start = 0
        if load_path:
            state_dict = torch.load(load_path, map_location=device)
            agent.load_state_dict(state_dict["agent"])
            ppo.optimizer.load_state_dict(state_dict["optimizer"])
            start = state_dict.get("step", -1) + 1
            # if isinstance(self.envs.venv, VecNormalize):
            #     self.envs.venv.load_state_dict(state_dict["vec_normalize"])
            print(f"Loaded parameters from {load_path}.")

        def report(**kwargs):
            if use_tune:
                tune.report(**kwargs)
            else:
                pprint(kwargs)

        class EpochCounter:
            def __init__(self):
                self.episode_rewards = []
                self.episode_time_steps = []
                self.rewards = np.zeros(num_processes)
                self.time_steps = np.zeros(num_processes)

            def update(self, reward, done):
                self.rewards += reward.numpy()
                self.time_steps += np.ones_like(done)
                self.episode_rewards += list(self.rewards[done])
                self.episode_time_steps += list(self.time_steps[done])
                self.rewards[done] = 0
                self.time_steps[done] = 0

        train_counter = EpochCounter()

        def run_epoch(obs, rnn_hxs, masks, envs):
            episode_counter = defaultdict(list)
            for _ in range(num_steps):
                with torch.no_grad():
                    act = self.agent(
                        inputs=obs, rnn_hxs=rnn_hxs, masks=masks
                    )  # type: AgentOutputs

                # Observe reward and next obs
                obs, reward, done, infos = envs.step(act.action)
                self.process_infos(episode_counter, done, infos, **act.log)

                # If done then clean the history of observations.
                masks = torch.tensor(
                    1 - done, dtype=torch.float32, device=obs.device
                ).unsqueeze(1)
                yield EpochOutputs(
                    obs=obs, reward=reward, done=done, infos=infos, act=act, masks=masks
                )

                rnn_hxs = act.rnn_hxs

        for _ in itertools.count():
            eval_counter = {}
            if eval_interval and not no_eval:
                # vec_norm = get_vec_normalize(eval_envs)
                # if vec_norm is not None:
                #     vec_norm.eval()
                #     vec_norm.ob_rms = get_vec_normalize(envs).ob_rms

                # self.envs.evaluate()
                eval_masks = torch.zeros(num_processes, 1, device=device)
                eval_envs = make_vec_envs(evaluation=True)
                eval_envs.to(device)
                with agent.network.evaluating(eval_envs.observation_space):
                    eval_recurrent_hidden_states = torch.zeros(
                        num_processes, agent.recurrent_hidden_state_size, device=device
                    )
                    eval_counter = EpochCounter()
                    for epoch_output in run_epoch(
                        obs=eval_envs.reset(),
                        rnn_hxs=eval_recurrent_hidden_states,
                        masks=eval_masks,
                        envs=eval_envs,
                    ):
                        eval_counter.update(
                            reward=epoch_output.reward, done=epoch_output.done
                        )

                eval_envs.close()
                eval_counter = dict(
                    eval_rewards=eval_counter.episode_rewards,
                    eval_time_steps=eval_counter.episode_time_steps,
                )
            # self.envs.train()
            obs = train_envs.reset()
            rollouts.obs[0].copy_(obs)
            tick = time.time()

            train_counter = EpochCounter()
            for i in itertools.count():
                for epoch_output in run_epoch(
                    obs=rollouts.obs[0],
                    rnn_hxs=rollouts.recurrent_hidden_states[0],
                    masks=rollouts.masks[0],
                    envs=train_envs,
                ):
                    train_counter.update(
                        reward=epoch_output.reward, done=epoch_output.done
                    )
                    rollouts.insert(
                        obs=epoch_output.obs,
                        recurrent_hidden_states=epoch_output.act.rnn_hxs,
                        actions=epoch_output.act.action,
                        action_log_probs=epoch_output.act.action_log_probs,
                        values=epoch_output.act.value,
                        rewards=epoch_output.reward,
                        masks=epoch_output.masks,
                    )

                with torch.no_grad():
                    next_value = agent.get_value(
                        rollouts.obs[-1],
                        rollouts.recurrent_hidden_states[-1],
                        rollouts.masks[-1],
                    ).detach()

                rollouts.compute_returns(next_value=next_value)
                train_results = ppo.update(rollouts)
                rollouts.after_update()
                if i % log_interval == 0:
                    total_num_steps = log_interval * num_processes * num_steps
                    fps = total_num_steps / (time.time() - tick)
                    tick = time.time()
                    result = dict(
                        tick=tick,
                        fps=fps,
                        rewards=train_counter.episode_rewards,
                        time_steps=train_counter.episode_time_steps,
                        **eval_counter,
                        **train_results,
                    )
                    total_num_steps = (i + 1) * num_processes * num_steps
                    for k, v in k_scalar_pairs(**result):
                        if self.writer:
                            self.writer.add_scalar(k, v, total_num_steps)
                    train_counter = EpochCounter()

    @staticmethod
    def process_infos(episode_counter, done, infos, **act_log):
        for d in infos:
            for k, v in d.items():
                episode_counter[k] += v if type(v) is list else [float(v)]
        for k, v in act_log.items():
            episode_counter[k] += v if type(v) is list else [float(v)]

    @staticmethod
    def build_agent(envs, **agent_args):
        return Agent(envs.observation_space.shape, envs.action_space, **agent_args)

    @staticmethod
    def make_env(env_id, seed, rank, evaluation):
        env = gym.make(env_id)
        env.seed(seed + rank)
        return env

    def _save(self, checkpoint_dir):
        modules = dict(
            optimizer=self.ppo.optimizer, agent=self.agent
        )  # type: Dict[str, torch.nn.Module]
        # if isinstance(self.envs.venv, VecNormalize):
        #     modules.update(vec_normalize=self.envs.venv)
        state_dict = {name: module.state_dict() for name, module in modules.items()}
        save_path = Path(checkpoint_dir, f"checkpoint.pt")
        torch.save(dict(step=self.i, **state_dict), save_path)
        print(f"Saved parameters to {save_path}")
        return str(save_path)

    def _restore(self, checkpoint):
        load_path = checkpoint
        state_dict = torch.load(load_path, map_location=self.device)
        self.agent.load_state_dict(state_dict["agent"])
        self.ppo.optimizer.load_state_dict(state_dict["optimizer"])
        self.i = state_dict.get("step", -1) + 1
        # if isinstance(self.envs.venv, VecNormalize):
        #     self.envs.venv.load_state_dict(state_dict["vec_normalize"])
        print(f"Loaded parameters from {load_path}.")