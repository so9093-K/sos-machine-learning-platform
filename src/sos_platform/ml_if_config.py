"""학습 제출·Fork·실행이 공유하는 IF 매개변수 계약. 실행 SDK에 의존하지 않는다."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class IsolationForestConfig:
    """실제로 지원하는 IF 학습 파라미터를 검증한다."""

    n_estimators: int = 100
    max_samples: int | float | Literal["auto"] = "auto"
    max_features: int | float = 1.0
    bootstrap: bool = False
    random_state: int = 42

    def __post_init__(self) -> None:
        if type(self.n_estimators) is not int or not 1 <= self.n_estimators <= 2000:
            raise ValueError("n_estimators must be an integer between 1 and 2000")
        if self.max_samples != "auto":
            if type(self.max_samples) is int:
                if self.max_samples < 2:
                    raise ValueError("Integer max_samples must be >= 2")
            elif type(self.max_samples) is float:
                if not 0 < self.max_samples <= 1:
                    raise ValueError("Float max_samples must be in (0, 1]")
            else:
                raise ValueError("Unsupported max_samples")
        if type(self.max_features) is int:
            if self.max_features < 1:
                raise ValueError("Integer max_features must be >= 1")
        elif type(self.max_features) is float:
            if not 0 < self.max_features <= 1:
                raise ValueError("Float max_features must be in (0, 1]")
        else:
            raise ValueError("Unsupported max_features")
        if type(self.bootstrap) is not bool:
            raise ValueError("bootstrap must be boolean")
        if type(self.random_state) is not int or not 0 <= self.random_state <= 2**32 - 1:
            raise ValueError("random_state must be an unsigned 32-bit integer")
