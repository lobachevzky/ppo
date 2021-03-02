import typing
from abc import abstractmethod, ABC, ABCMeta
from dataclasses import dataclass, astuple, replace, fields
from enum import unique, Enum, auto, EnumMeta
from functools import lru_cache
from typing import Tuple, Union, List, Generator, Dict, Optional

import gym
import numpy as np
import torch
from colored import fg
from gym import spaces

from data_types import Obs, RawAction
from utils import RESET

CoordType = Tuple[int, int]
IntGenerator = Generator[int, None, None]
IntListGenerator = Generator[List[int], None, None]
BoolGenerator = Generator[bool, None, None]

WORLD_SIZE = None


def move_from(origin: CoordType, toward: CoordType) -> CoordType:
    origin = np.array(origin)
    i, j = np.array(origin) + np.clip(
        np.array(toward) - origin,
        -1,
        1,
    )
    return i, j


class InvalidInput(Exception):
    pass


""" abstract classes """


class WorldObject:
    @property
    @abstractmethod
    def symbol(self):
        pass

    @abstractmethod
    def __eq__(self, other):
        pass


class Assignee:
    def __lt__(self, other: "Assignee"):
        return self.to_int() < other.to_int()

    @abstractmethod
    def to_int(self) -> int:
        pass


class ActionComponentMeta(type):
    pass


class ActionComponentEnumMeta(ActionComponentMeta, EnumMeta):
    pass


class ActionComponentABCMeta(ActionComponentMeta, ABCMeta):
    pass


class ActionComponent(metaclass=ActionComponentMeta):
    @staticmethod
    @abstractmethod
    def parse(n: int) -> "ActionComponent":
        pass

    @staticmethod
    @abstractmethod
    def space() -> spaces.Discrete:
        pass

    @abstractmethod
    def to_int(self) -> int:
        pass


ActionComponentGenerator = Generator[ActionComponent, None, None]


class Building(
    WorldObject, ActionComponent, Assignee, ABC, metaclass=ActionComponentABCMeta
):
    def __eq__(self, other):
        return type(self) == type(other)

    def __hash__(self):
        return hash(type)

    def __str__(self):
        return self.__class__.__name__

    def __repr__(self):
        return str(self)

    #     return f"({Buildings.index(self)}) {str(self)}"

    @property
    @abstractmethod
    def cost(self) -> "Resources":
        pass

    def on(self, coord: "CoordType", building_positions: "BuildingCounter"):
        return self == building_positions.get(coord)

    @staticmethod
    def parse(n: int) -> "Building":
        return Buildings[n]

    @staticmethod
    def space() -> spaces.Discrete:
        return spaces.Discrete(len(Buildings))

    @property
    @abstractmethod
    def symbol(self) -> str:
        pass

    def to_int(self) -> int:
        return Buildings.index(self)


class Assignment:
    @abstractmethod
    def execute(
        self,
        assignee: "Assignee",
        assignments: "Assignments",
        building_positions: "BuildingCounter",
        carrying: "Carrying",
        deployed_units: "UnitCounter",
        pending_costs: "ResourceCounter",
        pending_positions: "BuildingCounter",
        positions: "Positions",
        resources: "ResourceCounter",
    ) -> None:
        raise NotImplementedError


""" world objects"""


@unique
class Worker(
    WorldObject, ActionComponent, Assignee, Enum, metaclass=ActionComponentEnumMeta
):
    W1 = auto()
    W2 = auto()
    W3 = auto()

    # W4 = auto()
    # W5 = auto()
    # W6 = auto()
    # W7 = auto()
    # W8 = auto()
    # W9 = auto()
    # W10 = auto()
    # W11 = auto()
    # W12 = auto()

    def __eq__(self, other):
        # noinspection PyArgumentList
        return Enum.__eq__(self, other)

    def __lt__(self, other):
        if isinstance(other, Building):
            return True
        assert isinstance(other, Worker)
        # noinspection PyArgumentList
        return self.value < other.value

    def __hash__(self):
        # noinspection PyArgumentList
        return Enum.__hash__(self)

    def on(
        self,
        coord: "CoordType",
        positions: "Positions",
    ) -> bool:
        return positions[self] == coord

    @staticmethod
    def parse(n: int) -> "Worker":
        return Worker(n + 1)

    @staticmethod
    def space() -> spaces.Discrete:
        return spaces.Discrete(len(Worker))

    @property
    def symbol(self) -> str:
        return str(self.value)

    def to_int(self) -> int:
        return self.value - 1


AssigneeGenerator = Generator[Assignee, None, None]


@unique
class Resource(WorldObject, Assignment, Enum):
    MINERALS = auto()
    GAS = auto()

    def __hash__(self):
        return Enum.__hash__(self)

    def __eq__(self, other):
        return Enum.__eq__(self, other)

    def execute(
        self,
        assignee: "Assignee",
        building_positions: "BuildingCounter",
        carrying: "Carrying",
        positions: "Positions",
        resources: "ResourceCounter",
        **kwargs,
    ) -> None:
        assert isinstance(assignee, Worker)
        worker_pos = positions[assignee]

        if carrying[assignee] is None:
            resource_pos = positions[self]
            positions[assignee] = move_from(worker_pos, toward=resource_pos)
            worker_pos = positions[assignee]
            if worker_pos == resource_pos:
                carrying[assignee] = self
        raise NotImplementedError
        # else:
        #     nexus_positions: List[CoordType] = [
        #         p for p, b in building_positions.items() if isinstance(b, Nexus)
        #     ]
        #     nexus = get_nearest(nexus_positions, to=worker_pos)
        #     positions[assignee] = move_from(
        #         worker_pos,
        #         toward=nexus,
        #     )
        #     if positions[assignee] == nexus:
        #         resource = carrying[assignee]
        #         assert isinstance(resource, Resource)
        #         resources[resource] += 100
        #         carrying[assignee] = None
        # return None

    def on(
        self,
        coord: "CoordType",
        positions: "Positions",
    ) -> bool:
        return positions[self] == coord

    @property
    def symbol(self) -> str:
        if self is Resource.GAS:
            return fg("green") + "g" + RESET
        if self is Resource.MINERALS:
            return fg("blue") + "m" + RESET
        raise RuntimeError


ResourceCounter = typing.Counter[Resource]


@dataclass(frozen=True)
class Resources:
    minerals: int
    gas: int

    def __iter__(self):
        yield from [Resource.MINERALS] * self.minerals
        yield from [Resource.GAS] * self.gas


assert set(Resources(0, 0).__annotations__.keys()) == {
    r.lower() for r in Resource.__members__
}

""" action components """


@dataclass
class Coord(ActionComponent):
    i: int
    j: int

    @staticmethod
    def parse(n: int) -> "Coord":
        assert isinstance(WORLD_SIZE, int)
        ij = np.unravel_index(n, (WORLD_SIZE, WORLD_SIZE))
        return Coord(*ij)

    @staticmethod
    def possible_values():
        assert isinstance(WORLD_SIZE, int)
        for i in range(WORLD_SIZE):
            for j in range(WORLD_SIZE):
                yield i, j

    @staticmethod
    def space() -> spaces.Discrete:
        assert isinstance(WORLD_SIZE, int)
        return spaces.Discrete(WORLD_SIZE ** 2)

    def to_int(self) -> int:
        return int(np.ravel_multi_index((self.i, self.j), (WORLD_SIZE, WORLD_SIZE)))

    @staticmethod
    def zeros() -> IntGenerator:
        yield 0
        yield 0


# BuildingPositions = Dict[CoordType, Building]
BuildingCounter = typing.Counter[Building]
Positions = Dict[Union[Resource, Worker], CoordType]
Carrying = Dict[Worker, Optional[Resource]]
Assignments = Dict[Assignee, Assignment]


@dataclass(frozen=True)
class BuildOrder(Assignment):
    building: Building
    coord: CoordType

    def execute(
        self,
        assignee: "Assignee",
        assignments: "Assignments",
        building_positions: "BuildingCounter",
        pending_costs: "ResourceCounter",
        positions: "Positions",
        resources: "ResourceCounter",
        **kwargs,
    ) -> None:
        if positions[assignee] == self.coord:
            building_positions[self.coord] = self.building
            resources.subtract(pending_costs)
            assignments[assignee] = DoNothing()
            return None
        else:
            return GoTo(self.coord).execute(
                assignee=assignee,
                assignments=assignments,
                pending_costs=pending_costs,
                positions=positions,
                resources=resources,
                **kwargs,
            )


@dataclass(frozen=True)
class GoTo(Assignment):
    coord: CoordType

    def execute(self, assignee: "Assignee", positions: "Positions", **kwargs) -> None:
        assert isinstance(assignee, Worker)
        positions[assignee] = move_from(positions[assignee], toward=self.coord)
        return


class DoNothing(Assignment):
    def execute(self, **kwargs) -> None:
        return


Command = Union[BuildOrder, Resource]

O = typing.TypeVar("O", torch.Tensor, np.ndarray, int, gym.Space)


@dataclass(frozen=True)
class Action(RawAction):
    def is_op(self):
        return self.extrinsic is not None

    @classmethod
    def parse(
        cls,
        delta: int = 0,
        gate: int = 1,
        pointer1: int = 0,
        pointer2: int = 0,
        extrinsic: int = None,
    ) -> "Action":
        return Action(
            delta=delta,
            gate=gate,
            pointer1=pointer1,
            pointer2=pointer2,
            extrinsic=cls.parse_extrinsic(extrinsic),
        )

    @staticmethod
    def parse_extrinsic(extrinsic: int) -> Optional[Union[Building, "Unit"]]:
        if not extrinsic:
            return None
        else:
            extrinsic -= 1
            if Building.space().contains(extrinsic):
                return Building.parse(extrinsic)
            else:
                extrinsic -= Building.space().n
                return Unit.parse(extrinsic)

    @staticmethod
    def extrinsic_space() -> spaces.Discrete:
        return spaces.Discrete(1 + Building.space().n + Unit.space().n)

    @staticmethod
    def mask(
        unit_dependencies: Dict["Unit", Building], buildings: BuildingCounter
    ) -> np.ndarray:
        def mask():
            for _ in Buildings:
                yield 0
            for unit in Units:
                yield int(not buildings[unit_dependencies[unit]])

        return np.array([0, *mask()])

    def int_representation(self) -> int:
        if self.extrinsic is None:
            rep = 0
        elif isinstance(self.extrinsic, Building):
            rep = 1 + self.extrinsic.to_int()
        elif isinstance(self.extrinsic, Unit):
            rep = 1 + Building.space().n + self.extrinsic.to_int()
        else:
            raise RuntimeError
        assert self.parse_extrinsic(rep) == self.extrinsic
        return rep

    def invalid(
        self,
        unit_dependencies: Dict["Unit", Building],
        building_dependencies: Dict[Building, Building],
        buildings: BuildingCounter,
    ) -> Optional[str]:
        if isinstance(self.extrinsic, Building):
            dependency = building_dependencies.get(self.extrinsic)
            if dependency is not None and not buildings[dependency]:
                return "Dependency not built"
        if isinstance(self.extrinsic, Unit):
            assert buildings[unit_dependencies[self.extrinsic]]


@dataclass(frozen=True)
class CompoundAction:
    worker: Optional[Worker] = None
    building: Optional[Building] = None
    coord: Optional[Coord] = None
    unit: Optional["Unit"] = None

    @staticmethod
    def component_classes() -> Generator[type, None, None]:
        yield Worker
        yield Building
        yield Coord
        yield Unit

    @classmethod
    def input_space(cls):
        def space_size(c):
            assert issubclass(c, ActionComponent)
            return c.space().n

        return spaces.MultiDiscrete([1 + sum(map(space_size, cls.component_classes()))])

    @classmethod
    def parse(cls, value: int):
        worker = building = coord = unit = None

        def parse_alternatives(n, *classes: ActionComponentMeta):
            assert n >= 0
            head, *tail = classes
            nones = [None for _ in tail]
            assert issubclass(head, ActionComponent)
            if head.space().contains(n):
                return [head.parse(n), *nones]
            return [None, *parse_alternatives(n - head.space().n, *tail)]

        if value > 0:
            return CompoundAction(
                *parse_alternatives(value - 1, *cls.component_classes())
            )

        return CompoundAction(worker=worker, building=building, coord=coord, unit=unit)

    @classmethod
    def representation_space(cls):
        # noinspection PyUnresolvedReferences
        return spaces.MultiDiscrete([1 + c.space().n for c in cls.component_classes()])

    def to_input_int(self) -> IntGenerator:
        base = 1
        for f, cls in zip(fields(self), self.component_classes()):
            assert issubclass(cls, ActionComponent)
            value = getattr(self, f.name)
            if value is not None:
                assert isinstance(value, cls)
                yield base + value.to_int()
                return
            base += cls.space().n
        yield 0

    def to_representation_ints(self) -> IntGenerator:
        value: Optional[ActionComponent]
        for f in fields(self):
            value = getattr(self, f.name)
            yield 0 if value is None else value.to_int()


CompoundActionGenerator = Generator[CompoundAction, None, None]


@dataclass(frozen=True)
class ActionStage:
    @staticmethod
    def _children() -> List[type]:
        return [
            InitialAction,
            WorkerAction,
            BuildingAction,
            WorkerBuildingAction,
            WorkerCoordAction,
            BuildingCoordAction,
            UnitAction,
        ]

    @staticmethod
    @abstractmethod
    def _gate_openers() -> CompoundActionGenerator:
        pass

    @staticmethod
    @abstractmethod
    def _parse_string(s: str) -> CompoundAction:
        pass

    @staticmethod
    @abstractmethod
    def _permitted_values(
        unit_dependencies: Dict["Unit", Building]
    ) -> CompoundActionGenerator:
        pass

    @staticmethod
    @abstractmethod
    def _prompt() -> str:
        pass

    @staticmethod
    @abstractmethod
    def _update(
        action: CompoundAction, building_positions: BuildingCounter
    ) -> "ActionStage":
        pass

    @abstractmethod
    def compound_action(self) -> CompoundAction:
        pass

    @abstractmethod
    def assignment(self, positions: Positions) -> Optional[Assignment]:
        pass

    def from_input(self, building_positions: BuildingCounter) -> "ActionStage":
        compound_action = None
        while compound_action is None:
            string = input(self._prompt() + "\n")
            if string:
                try:
                    compound_action = self._parse_string(string)
                except InvalidInput as e:
                    print(e)
                if (
                    isinstance(compound_action, BuildingAction)
                    and compound_action.building not in building_positions.values()
                ):
                    print(f"{compound_action.building} is not yet built.")
            else:
                compound_action = CompoundAction()
        return self.__update(compound_action, building_positions=building_positions)

    @classmethod
    @lru_cache
    def gate_openers(cls) -> np.ndarray:
        return np.array([list(o.to_input_int()) for o in cls._gate_openers()])

    @classmethod
    def gate_opener_max_size(cls):
        def opener_size():
            for c in cls._children():
                assert issubclass(c, ActionStage)
                yield len(c.gate_openers())

        return max(opener_size())

    @abstractmethod
    def assignee(self) -> AssigneeGenerator:
        raise NotImplementedError

    def invalid(
        self,
        resources: typing.Counter[Resource],
        dependencies: Dict[Building, Building],
        building_positions: BuildingCounter,
        pending_costs: ResourceCounter,
        pending_positions: BuildingCounter,
        positions: Positions,
        unit_dependencies: Dict["Unit", Building],
    ) -> Optional[str]:
        return

    def mask(self, unit_dependencies: Dict["Unit", Building]) -> np.ndarray:
        nvec = CompoundAction.input_space().nvec
        mask = np.ones((len(nvec), max(nvec)))
        r = np.arange(len(nvec))
        for permitted_values in [
            *self._permitted_values(unit_dependencies),
            CompoundAction(),
        ]:
            unmask = [*permitted_values.to_input_int()]
            mask[r, unmask] = 0
        return mask

    def to_ints(self):
        return self.compound_action().to_representation_ints()

    def update(
        self, *components: int, building_positions: BuildingCounter
    ) -> "ActionStage":
        compound_action = CompoundAction.parse(*components)
        return self.__update(compound_action, building_positions)

    def __update(
        self, compound_action: CompoundAction, building_positions: BuildingCounter
    ):
        if not any(astuple(compound_action)):
            return InitialAction()
        return self._update(compound_action, building_positions=building_positions)


class CoordCanOpenGate(ActionStage, ABC):
    @staticmethod
    def _gate_openers() -> CompoundActionGenerator:
        for i, j in Coord.possible_values():
            yield CompoundAction(coord=Coord(i, j))


@dataclass(frozen=True)
class InitialAction(ActionStage):
    @staticmethod
    def _gate_openers() -> CompoundActionGenerator:
        # selecting no workers or coords is a no-op that allows gate to open
        yield CompoundAction()

    @staticmethod
    def _parse_string(s: str) -> CompoundAction:
        coord = worker = None
        if not s:
            return CompoundAction(worker=worker, coord=coord)
        try:
            i, j = map(int, s.split())
        except ValueError:
            try:
                worker = Worker(int(s))
            except ValueError:
                raise InvalidInput
            return CompoundAction(worker=worker, coord=coord)
        coord = Coord(i, j)
        return CompoundAction(worker=worker, coord=coord)

    @staticmethod
    def _permitted_values(unit_dependencies) -> CompoundActionGenerator:
        for worker in Worker:
            yield CompoundAction(worker=worker)
        for coord in Coord.possible_values():
            yield CompoundAction(coord=Coord(*coord))

    @staticmethod
    def _prompt() -> str:
        return "Worker or Coord:"

    def _update(
        self, action: CompoundAction, building_positions: BuildingCounter
    ) -> Union["WorkerAction", "BuildingAction", "InitialAction"]:
        if action.worker is not None:
            return WorkerAction(action.worker)
        if action.coord is not None:
            coord = astuple(action.coord)
            if coord in building_positions:
                return BuildingAction(building_positions[coord])
        return InitialAction()

    def assignment(self, positions: Positions) -> Optional[Assignment]:
        return DoNothing()

    def assignee(self) -> AssigneeGenerator:
        yield from ()

    def compound_action(self) -> CompoundAction:
        return CompoundAction()


@dataclass(frozen=True)
class HasWorker(ActionStage, ABC):
    worker: Worker

    def compound_action(self) -> CompoundAction:
        return CompoundAction(worker=self.worker)

    def assignee(self) -> AssigneeGenerator:
        yield self.worker


@dataclass(frozen=True)
class WorkerAction(HasWorker, CoordCanOpenGate):
    @staticmethod
    def _parse_string(s: str) -> CompoundAction:
        try:
            i, j = map(int, s.split())
        except ValueError:
            try:
                n = int(s)
            except ValueError:
                raise InvalidInput
            try:
                building = Buildings[n]
            except IndexError as e:
                raise InvalidInput(e)
            return CompoundAction(building=building)
        return CompoundAction(coord=Coord(i, j))

    @staticmethod
    def _permitted_values(unit_dependencies) -> CompoundActionGenerator:
        for i, j in Coord.possible_values():
            yield CompoundAction(coord=Coord(i, j))
        for building in Buildings:
            yield CompoundAction(building=building)

    @staticmethod
    def _prompt() -> str:
        return "\n".join(
            [f"({i}) {b}" for i, b in enumerate(Buildings)] + ["Coord or Building"]
        )

    def _update(
        self, action: CompoundAction, building_positions: BuildingCounter
    ) -> "ActionStage":
        if (action.coord, action.building) == (None, None):
            assert action.worker is None
            return InitialAction()
        if action.coord is not None:
            assert action.worker is None and action.building is None
            return WorkerCoordAction(worker=self.worker, coord=action.coord)
        if action.building is not None:
            assert action.worker is None and action.coord is None
            return WorkerBuildingAction(worker=self.worker, building=action.building)
        raise RuntimeError

    def assignment(self, positions: Positions) -> Optional[Assignment]:
        return None


@dataclass(frozen=True)
class BuildingAction(ActionStage):
    building: Building

    @staticmethod
    def _gate_openers() -> CompoundActionGenerator:
        for unit in Units:
            yield CompoundAction(unit=unit)

    @staticmethod
    def _parse_string(s: str) -> CompoundAction:
        try:
            i = int(s)
        except ValueError:
            raise InvalidInput
        try:
            unit = Unit.parse(i)
        except IndexError:
            raise InvalidInput
        return CompoundAction(unit=unit)

    def _permitted_values(self, unit_dependencies) -> CompoundActionGenerator:
        for unit in Units:
            if unit_dependencies[unit] == self.building:
                yield CompoundAction(unit=unit)

    @staticmethod
    def _prompt() -> str:
        return "\n".join(
            [f"({i}) {u}" for i, u in enumerate(Units)]
            + ["Unit (dependency must have been built):"]
        )

    def _update(
        self, action: CompoundAction, building_positions: BuildingCounter
    ) -> "ActionStage":
        if action.unit is None:
            return InitialAction()
        return UnitAction(building=self.building, unit=action.unit)

    def compound_action(self) -> CompoundAction:
        return CompoundAction(building=self.building)

    def assignment(self, positions: Positions) -> Optional[Assignment]:
        return None

    def assignee(self) -> AssigneeGenerator:
        yield from self.building

    def invalid(
        self,
        building_positions: BuildingCounter,
        **kwargs,
    ) -> Optional[str]:
        if self.building not in building_positions.values():
            return f"{self.building} not built."
        return None


@dataclass(frozen=True)
class WorkerCoordAction(HasWorker, InitialAction):
    coord: Coord

    def compound_action(self) -> CompoundAction:
        return replace(HasWorker.compound_action(self), coord=self.coord)

    def assignment(self, positions: Positions) -> Optional[Assignment]:
        i, j = astuple(self.coord)
        for resource in Resource:
            if resource.on((i, j), positions):
                return resource
        return GoTo((i, j))

    def invalid(
        self,
        building_positions: BuildingCounter,
        positions: Positions,
        **kwargs,
    ) -> Optional[str]:
        coord = astuple(self.coord)
        built_at_destination = building_positions.get(coord)
        if (
            positions[Resource.GAS] == coord
            and not built_at_destination == Assimilator()
        ):
            return "Assimilator required for harvesting gas"  # no op on gas unless Assimilator


@dataclass(frozen=True)
class WorkerBuildingAction(HasWorker, CoordCanOpenGate):
    building: Building

    @staticmethod
    def _parse_string(s: str) -> CompoundAction:
        try:
            i, j = map(int, s.split())
        except ValueError:
            raise InvalidInput
        return CompoundAction(coord=Coord(i, j))

    @staticmethod
    def _permitted_values(unit_dependencies) -> CompoundActionGenerator:
        for i, j in Coord.possible_values():
            yield CompoundAction(coord=Coord(i, j))

    @staticmethod
    def _prompt() -> str:
        return "Coord"

    def _update(
        self, action: CompoundAction, building_positions: BuildingCounter
    ) -> "ActionStage":
        assert not any([action.worker, action.building])
        if action.coord is None:
            return InitialAction()
        return BuildingCoordAction(
            worker=self.worker, building=self.building, coord=action.coord
        )

    def compound_action(self) -> CompoundAction:
        return replace(HasWorker.compound_action(self), building=self.building)

    def assignment(self, positions: Positions) -> Optional[Assignment]:
        return None

    def invalid(
        self,
        resources: typing.Counter[Resource],
        dependencies: Dict[Building, Building],
        building_positions: BuildingCounter,
        pending_costs: ResourceCounter,
        **kwargs,
    ) -> Optional[str]:
        dependency = dependencies[self.building]
        dependency_met = dependency in [*building_positions.values(), None]
        if not dependency_met:
            return f"Dependency ({dependency}) not met for {self}."
        # insufficient_resources = Counter(self.building.cost) - resources - pending_costs
        # if insufficient_resources:
        #     return "Insufficient resources"
        return None


@dataclass(frozen=True)
class BuildingCoordAction(HasWorker, InitialAction):
    building: Building
    coord: Coord

    def compound_action(self) -> CompoundAction:
        return replace(
            HasWorker.compound_action(self), coord=self.coord, building=self.building
        )

    def assignment(self, positions: Positions) -> Assignment:
        i, j = astuple(self.coord)
        on_gas = Resource.GAS.on((i, j), positions)
        assimilator = isinstance(self.building, Assimilator)
        assert (on_gas and assimilator) or (not on_gas and not assimilator)
        assert not Resource.MINERALS.on((i, j), positions)
        return BuildOrder(self.building, (i, j))

    def assignee(self) -> AssigneeGenerator:
        yield self.worker

    def invalid(
        self,
        dependencies: Dict[Building, Building],
        building_positions: BuildingCounter,
        pending_positions: BuildingCounter,
        positions: Positions,
        **kwargs,
    ) -> Optional[str]:
        dependency = dependencies[self.building]
        if dependency not in [*building_positions.values(), None]:
            return f"Dependency ({dependency}) not met for {self.building}."
        coord = astuple(self.coord)
        all_positions = {**building_positions, **pending_positions}
        if coord in all_positions:
            return f"coord occupied by {all_positions[coord]}"
        if isinstance(self.building, Assimilator):
            return (
                None
                if coord == positions[Resource.GAS]
                else f"Assimilator not built on gas"
            )
        else:
            return (
                "Building built on resource"
                if coord
                in (
                    positions[Resource.GAS],
                    positions[Resource.MINERALS],
                )
                else None
            )


@dataclass(frozen=True)
class UnitAction(InitialAction):
    building: Building
    unit: "Unit"

    def assignee(self) -> AssigneeGenerator:
        yield self.building

    def assignment(self, positions: Positions) -> Optional[Assignment]:
        return self.unit


# Check that fields are alphabetical. Necessary because of the way
# that observation gets vectorized.
annotations = Obs.__annotations__
assert tuple(annotations) == tuple(sorted(annotations))


@dataclass
class State:
    # action: "Action"
    agent_pointer: int
    success: bool
    buildings: BuildingCounter
    required_units: "UnitCounter"
    resources: ResourceCounter
    destroyed_unit: Optional["Unit"]
    time_remaining: int
    no_ops_remaining: int


def get_nearest(
    candidate_positions: List[CoordType],
    to: CoordType,
) -> CoordType:
    nearest = np.argmin(
        np.max(
            np.abs(
                np.expand_dims(np.array(to), 0) - np.stack(candidate_positions),
            ),
            axis=-1,
        )
    )
    return candidate_positions[int(nearest)]


class Assimilator(Building):
    @property
    def cost(self) -> Resources:
        return Resources(minerals=75, gas=0)

    @property
    def symbol(self) -> str:
        return "A"


class CyberneticsCore(Building):
    @property
    def cost(self) -> Resources:
        return Resources(minerals=150, gas=0)

    @property
    def symbol(self) -> str:
        return "CC"


class DarkShrine(Building):
    @property
    def cost(self) -> Resources:
        return Resources(minerals=150, gas=150)

    @property
    def symbol(self) -> str:
        return "DS"


class FleetBeacon(Building):
    @property
    def cost(self) -> Resources:
        return Resources(minerals=300, gas=200)

    @property
    def symbol(self) -> str:
        return "FB"


class Forge(Building):
    @property
    def cost(self) -> Resources:
        return Resources(minerals=150, gas=0)

    @property
    def symbol(self) -> str:
        return "f"


class Gateway(Building):
    @property
    def cost(self) -> Resources:
        return Resources(minerals=150, gas=0)

    @property
    def symbol(self) -> str:
        return "GW"


class Nexus(Building):
    @property
    def cost(self) -> Resources:
        return Resources(minerals=400, gas=0)

    @property
    def symbol(self) -> str:
        return "N"


class PhotonCannon(Building):
    @property
    def cost(self) -> Resources:
        return Resources(minerals=150, gas=0)

    @property
    def symbol(self) -> str:
        return "PC"


class Pylon(Building):
    @property
    def cost(self) -> Resources:
        return Resources(minerals=100, gas=0)

    @property
    def symbol(self) -> str:
        return "P"


class RoboticsBay(Building):
    @property
    def cost(self) -> Resources:
        return Resources(minerals=200, gas=200)

    @property
    def symbol(self) -> str:
        return "RB"


class RoboticsFacility(Building):
    @property
    def cost(self) -> Resources:
        return Resources(minerals=200, gas=100)

    @property
    def symbol(self) -> str:
        return "RF"


class StarGate(Building):
    @property
    def cost(self) -> Resources:
        return Resources(minerals=150, gas=150)

    @property
    def symbol(self) -> str:
        return "SG"


class TemplarArchives(Building):
    @property
    def cost(self) -> Resources:
        return Resources(minerals=150, gas=200)

    @property
    def symbol(self) -> str:
        return "TA"


class TwilightCouncil(Building):
    @property
    def cost(self) -> Resources:
        return Resources(minerals=150, gas=100)

    @property
    def symbol(self) -> str:
        return "TC"


Buildings: List[Building] = [
    Assimilator(),
    # CyberneticsCore(),
    # DarkShrine(),
    # FleetBeacon(),
    # Forge(),
    # Gateway(),
    Nexus(),
    # PhotonCannon(),
    # Pylon(),
    # RoboticsBay(),
    RoboticsFacility(),
    StarGate(),
    TemplarArchives(),
    TwilightCouncil(),
]


class Unit(ActionComponent, Assignment, ABC, metaclass=ActionComponentABCMeta):
    def __eq__(self, other):
        return type(self) == type(other)

    def __hash__(self):
        return hash(type)

    def __str__(self):
        return self.__class__.__name__

    def __repr__(self):
        return str(self)

    def execute(
        self,
        assignee: "Assignee",
        assignments: "Assignments",
        building_positions: "BuildingCounter",
        carrying: "Carrying",
        deployed_units: "UnitCounter",
        pending_costs: ResourceCounter,
        pending_positions: "BuildingCounter",
        positions: "Positions",
        resources: "ResourceCounter",
    ) -> None:
        deployed_units.update([self])
        del assignments[assignee]

    @property
    @abstractmethod
    def resource_cost(self) -> "Resources":
        pass

    @property
    @abstractmethod
    def population_cost(self) -> int:
        pass

    @staticmethod
    def parse(n: int) -> "Unit":
        return Units[n]

    @staticmethod
    def space() -> spaces.Discrete:
        return spaces.Discrete(len(Units))

    @lru_cache
    def to_int(self) -> int:
        return Units.index(self)


class Adept(Unit):
    @property
    def resource_cost(self) -> "Resources":
        return Resources(minerals=100, gas=25)

    @property
    def population_cost(self) -> int:
        return 2


class Carrier(Unit):
    @property
    def resource_cost(self) -> "Resources":
        return Resources(minerals=350, gas=250)

    @property
    def population_cost(self) -> int:
        return 6


class Colossus(Unit):
    @property
    def resource_cost(self) -> "Resources":
        return Resources(minerals=300, gas=200)

    @property
    def population_cost(self) -> int:
        return 6


class DarkTemplar(Unit):
    @property
    def resource_cost(self) -> "Resources":
        return Resources(minerals=125, gas=125)

    @property
    def population_cost(self) -> int:
        return 2


class Disruptor(Unit):
    @property
    def resource_cost(self) -> "Resources":
        return Resources(minerals=150, gas=150)

    @property
    def population_cost(self) -> int:
        return 3


class HighTemplar(Unit):
    @property
    def resource_cost(self) -> "Resources":
        return Resources(minerals=50, gas=150)

    @property
    def population_cost(self) -> int:
        return 2


class Immortal(Unit):
    @property
    def resource_cost(self) -> "Resources":
        return Resources(minerals=275, gas=100)

    @property
    def population_cost(self) -> int:
        return 4


class Observer(Unit):
    @property
    def resource_cost(self) -> "Resources":
        return Resources(minerals=25, gas=75)

    @property
    def population_cost(self) -> int:
        return 1


class Oracle(Unit):
    @property
    def resource_cost(self) -> "Resources":
        return Resources(minerals=150, gas=150)

    @property
    def population_cost(self) -> int:
        return 3


class Phoenix(Unit):
    @property
    def resource_cost(self) -> "Resources":
        return Resources(minerals=150, gas=100)

    @property
    def population_cost(self) -> int:
        return 2


class Sentry(Unit):
    @property
    def resource_cost(self) -> "Resources":
        return Resources(minerals=50, gas=100)

    @property
    def population_cost(self) -> int:
        return 2


class Stalker(Unit):
    @property
    def resource_cost(self) -> "Resources":
        return Resources(minerals=125, gas=50)

    @property
    def population_cost(self) -> int:
        return 2


class Tempest(Unit):
    @property
    def resource_cost(self) -> "Resources":
        return Resources(minerals=250, gas=175)

    @property
    def population_cost(self) -> int:
        return 5


class VoidRay(Unit):
    @property
    def resource_cost(self) -> "Resources":
        return Resources(minerals=200, gas=150)

    @property
    def population_cost(self) -> int:
        return 4


class WarpPrism(Unit):
    @property
    def resource_cost(self) -> "Resources":
        return Resources(minerals=250, gas=0)

    @property
    def population_cost(self) -> int:
        return 2


class Zealot(Unit):
    @property
    def resource_cost(self) -> "Resources":
        return Resources(minerals=100, gas=0)

    @property
    def population_cost(self) -> int:
        return 2


Units = [
    Adept(),
    Carrier(),
    Colossus(),
    DarkTemplar(),
    Disruptor(),
    HighTemplar(),
    # Immortal(),
    # Observer(),
    # Oracle(),
    # Phoenix(),
    # Sentry(),
    # Stalker(),
    # Tempest(),
    # VoidRay(),
    # WarpPrism(),
    # Zealot(),
]
UnitCounter = typing.Counter[Unit]
UnitSet = typing.Set[Unit]

WorldObjects = list(Buildings) + list(Resource) + list(Worker)
Line = Union[Building, Unit]