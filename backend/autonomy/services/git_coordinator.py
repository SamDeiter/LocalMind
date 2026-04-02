
import logging
from backend.git_ops import git_run, revert_file, run_tests

logger = logging.getLogger('localmind.autonomy.services.git')

class GitCoordinator:
    def __init__(self, engine):
        self.engine = engine

    def create_sandbox_branch(self, suffix: str) -> str:
        branch_name = f'self-improve/{suffix}'
        git_run(['checkout', '-b', branch_name])
        return branch_name

    def commit_and_merge(self, branch_name: str, message: str):
        git_run(['add', '-A'])
        git_run(['commit', '-m', f'[autonomy] {message}'])
        git_run(['checkout', 'main'])
        git_run(['merge', branch_name])

    def revert_and_cleanup(self, branch_name: str, edited_files: list):
        for f in edited_files:
            revert_file(f)
        git_run(['checkout', 'main'])
        git_run(['branch', '-D', branch_name])

    async def run_tests(self, target_files: list):
        return await run_tests(target_files=target_files)
