from rl_utils import hierarchical_parse_args

import ppo.agent
import ppo.control_flow.env
import ppo.control_flow.ours
import ppo.control_flow.baseline
from ppo import control_flow
from ppo.arguments import build_parser
from ppo.train import Train


def main(log_dir, seed, **kwargs):
    class _Train(Train):
        def build_agent(self, envs, debug=False, baseline=False, **agent_args):
            if baseline:
                return ppo.control_flow.baseline.Agent(
                    observation_space=envs.observation_space,
                    action_space=envs.action_space,
                    debug=debug,
                    **agent_args,
                )
            else:
                return ppo.control_flow.ours.Agent(
                    observation_space=envs.observation_space,
                    action_space=envs.action_space,
                    debug=debug,
                    **agent_args,
                )

        @staticmethod
        def make_env(seed, rank, evaluation, env_id, add_timestep, **env_args):
            return control_flow.env.Env(**env_args, baseline=False, seed=seed + rank)

    _Train(**kwargs, seed=seed, log_dir=log_dir).run()


def bandit_args():
    parsers = build_parser()
    parser = parsers.main
    parser.add_argument("--no-tqdm", dest="use_tqdm", action="store_false")
    parser.add_argument("--time-limit", type=int)
    parser.add_argument("--eval-steps", type=int)
    parsers.env.add_argument("--min-lines", type=int, required=True)
    parsers.env.add_argument("--max-lines", type=int, required=True)
    parsers.env.add_argument("--eval-lines", type=int)
    parsers.env.add_argument("--flip-prob", type=float, required=True)
    parsers.env.add_argument("--delayed-reward", action="store_true")
    parsers.agent.add_argument("--debug", action="store_true")
    parsers.agent.add_argument("--reduction", choices=["sum", "mean", "max"])
    parsers.agent.add_argument("--baseline", action="store_true")
    return parser


if __name__ == "__main__":
    main(**hierarchical_parse_args(bandit_args()))
