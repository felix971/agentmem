"""DESIGN 12.6: real temporary Git worktrees, never mocked Git output."""

from tests.helpers import AgentmemCase


class ProjectIdentityTests(AgentmemCase):
    def test_two_worktrees_and_clone_share_root_commit_project_id(self):
        repository = self.create_git_repo(self.root / "repository")
        first = self.root / "worktree-one"
        second = self.root / "worktree-two"
        clone = self.root / "clone"
        self.git("worktree", "add", "--quiet", "-b", "fixture-one", first, "HEAD", cwd=repository)
        self.git("worktree", "add", "--quiet", "-b", "fixture-two", second, "HEAD", cwd=repository)
        self.git("clone", "--quiet", repository, clone, cwd=self.root)
        roots = self.git("rev-list", "--max-parents=0", "HEAD", cwd=repository).splitlines()
        self.assertTrue(roots)
        expected = "git:" + sorted(roots)[0][:12]
        for path in (repository, first, second, clone):
            with self.subTest(path=path.name):
                self.assertEqual(self.project_id(path), expected)

    def test_existing_non_git_directory_uses_real_absolute_path(self):
        self.assertEqual(self.project_id(self.project), "path:" + str(self.project.resolve()))
        self.assertEqual(self.project_id(), "path:" + str(self.project.resolve()))

    def test_nonexistent_directory_uses_normalized_absolute_path(self):
        missing = self.root / "never-created" / "child"
        self.assertFalse(missing.exists())
        self.assertEqual(self.project_id(missing), "path:" + str(missing))

    def test_existing_directory_symlink_resolves_to_same_path_id(self):
        alias = self.root / "project-alias"
        alias.symlink_to(self.project, target_is_directory=True)
        self.assertEqual(self.project_id(alias), "path:" + str(self.project.resolve()))
