"""BUG-55: the container needs an init as PID 1.

The app shells out to git; git spawns ssh for SSH remotes and exits
first; the orphaned ssh re-parents to PID 1. With the application itself
as PID 1 — which never ``wait()``s on children it did not spawn — each
one stayed a zombie holding a task slot for the life of the container.
One instance reached 115 of them at roughly 26 a day, and the count only
grows: the end state is a host that cannot ``fork()``, recoverable only
by rebooting it.

A deployment can set ``init: true`` itself, and the one that found this
did. That is the wrong place for the fix — the published image is run by
people who will not know to — so these assert the image carries its own.
"""

from pathlib import Path

import pytest

DOCKERFILE = Path(__file__).resolve().parents[2] / "Dockerfile"


@pytest.fixture(scope="module")
def dockerfile() -> str:
    assert DOCKERFILE.is_file(), f"expected a Dockerfile at {DOCKERFILE}"
    return DOCKERFILE.read_text()


class TestTheImageReapsItsOrphans:
    def test_an_init_is_installed(self, dockerfile):
        assert " tini " in dockerfile, "the runtime stage does not install an init"

    def test_the_init_is_pid_1(self, dockerfile):
        """``ENTRYPOINT`` is what makes it PID 1. Installing tini and then
        not running it would leave the leak exactly where it was."""
        assert 'ENTRYPOINT ["/usr/bin/tini", "--"]' in dockerfile

    def test_the_app_is_still_the_command(self, dockerfile):
        """tini is the wrapper, not a replacement: ``docker run <image>``
        must still start LabDog, and ``docker run <image> <other>`` must
        still work — which is why the app stays in CMD rather than being
        folded into the ENTRYPOINT."""
        assert 'CMD ["python", "-m", "app"]' in dockerfile

    def test_the_entrypoint_comes_before_the_command(self, dockerfile):
        entry = dockerfile.index("ENTRYPOINT [")
        cmd = dockerfile.index('CMD ["python", "-m", "app"]')
        assert entry < cmd
