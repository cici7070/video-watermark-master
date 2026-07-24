from dataclasses import dataclass, field
from pathlib import Path
import secrets
import shutil
import tempfile


@dataclass
class JobWorkspace:
    path: Path
    _owned_path: Path | None = field(default=None, init=False, repr=False)
    _ownership_token: str | None = field(default=None, init=False, repr=False)

    _MARKER_NAME = ".watermark-master-owner"

    @classmethod
    def create(cls, root: Path | None = None) -> "JobWorkspace":
        path = Path(tempfile.mkdtemp(prefix="watermark-master-", dir=root))
        workspace = cls(path.resolve())
        workspace._owned_path = workspace.path
        workspace._ownership_token = secrets.token_urlsafe(32)
        workspace._marker_path.write_text(workspace._ownership_token, encoding="utf-8")
        return workspace

    def cleanup(self) -> None:
        if self._owned_path is None or self._ownership_token is None:
            return

        try:
            resolved_path = self.path.resolve(strict=True)
            marker_token = self._marker_path.read_text(encoding="utf-8")
        except OSError:
            return

        if (
            resolved_path != self._owned_path
            or not resolved_path.is_dir()
            or not secrets.compare_digest(marker_token, self._ownership_token)
        ):
            return

        shutil.rmtree(resolved_path)

    @property
    def _marker_path(self) -> Path:
        return self.path / self._MARKER_NAME
