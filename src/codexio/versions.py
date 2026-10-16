"""Semantic app versions; platform bundle/file versions use only the numeric core."""
from dataclasses import dataclass
from functools import total_ordering
import re

_PATTERN = re.compile(r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?\Z")


@total_ordering
@dataclass(frozen=True, eq=False)
class AppVersion:
    core: tuple[int, int, int]
    prerelease: tuple[str, ...] = ()
    metadata: str = ""

    @classmethod
    def parse(cls, value: str):
        match = _PATTERN.fullmatch(value) if isinstance(value, str) and len(value) <= 128 else None
        if match is None:
            raise ValueError("版本号无效，例如 0.3.10 或 0.3.10-beta")
        core = tuple(int(part) for part in match.groups()[:3])
        pre = tuple(match[4].split(".")) if match[4] else ()
        if any(part > 65535 for part in core) or any(p.isdigit() and len(p) > 1 and p[0] == "0" for p in pre):
            raise ValueError("版本数字超出范围或预发布序号含前导零")
        return cls(core, pre, match[5] or "")

    @property
    def numeric(self):
        return ".".join(map(str, self.core))

    def __str__(self):
        return self.numeric + ("-" + ".".join(self.prerelease) if self.prerelease else "") + ("+" + self.metadata if self.metadata else "")

    def __eq__(self, other):
        if not isinstance(other, AppVersion):
            return NotImplemented
        return (self.core, self.prerelease) == (other.core, other.prerelease)

    def __lt__(self, other):
        if not isinstance(other, AppVersion):
            return NotImplemented
        if self.core != other.core:
            return self.core < other.core
        if not self.prerelease or not other.prerelease:
            return bool(self.prerelease) and not other.prerelease
        for left, right in zip(self.prerelease, other.prerelease):
            if left == right:
                continue
            if left.isdigit() and right.isdigit():
                return int(left) < int(right)
            if left.isdigit() != right.isdigit():
                return left.isdigit()
            return left < right
        return len(self.prerelease) < len(other.prerelease)
