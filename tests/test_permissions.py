"""DESIGN 12.10 / section 0: inspect documented filesystem permissions."""

import stat

from tests.helpers import AgentmemCase


class PermissionsTests(AgentmemCase):
    def test_home_database_lock_and_notes_are_private_even_with_parent_umask_022(self):
        self.assertFalse(self.home.exists())
        self.seed_claude("PermissionFixtureMarker")
        # CLI helpers deliberately start children with umask 022, so a private
        # invoking shell cannot hide an implementation that forgot umask 077.
        self.index()
        self.assertEqual(stat.S_IMODE(self.home.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.home / "agentmem.db").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.home / "index.lock").stat().st_mode), 0o600)
        path, _, _ = self.notes_fixture()
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        for suffix in ("-wal", "-shm"):
            auxiliary = self.home / ("agentmem.db" + suffix)
            if auxiliary.exists():
                self.assertEqual(stat.S_IMODE(auxiliary.stat().st_mode), 0o600)
