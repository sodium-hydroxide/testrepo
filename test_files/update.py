#!/usr/bin/env python3
__version__ = "0.1.0"

import argparse
import logging
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Optional, Sequence, Union

# --- Logging setup ------------------------------------------------------------
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")

# --- Version check -----------------------------------------------------------
if sys.version_info < (3, 9):
    raise RuntimeError("Python >= 3.9 required")

# --- Paths -------------------------------------------------------------------
HOME = Path.home()
DOTFILES = HOME / "dotfiles"
PACKAGE_LIST = DOTFILES / "package-list"
STOW_IGNORE: list[str] = []  # to be populated later


# --- Exceptions --------------------------------------------------------------
# fmt: off
class PackagingError(Exception): pass # noqa: E701
class BootstrapError(PackagingError): pass # noqa: E701
class UpdateError(PackagingError): pass # noqa: E701
class InstallError(PackagingError): pass # noqa: E701
class CleanupError(PackagingError): pass # noqa: E701
class UninstallError(PackagingError): pass # noqa: E701
class NotInstalledError(PackagingError): pass # noqa: E701
# fmt: on


# --- Command wrapper --------------------------------------------------------
class Command:
    def __init__(
        self,
        program: Union[str, Sequence[str]],
        arguments: Optional[Union[str, Sequence[str]]] = None,
        sudo: bool = False,
    ) -> None:
        # Determine which executable to use
        candidates = [program] if isinstance(program, str) else list(program)
        for prog in candidates:
            if shutil.which(prog):
                self.program = prog
                break
        else:
            raise FileNotFoundError(
                f"Could not find any executables in {candidates}"
            )
        self.arguments = arguments
        self.sudo = sudo

    @property
    def argv(self) -> Sequence[str]:
        # Build the base command
        if self.arguments is None:
            cmd = [self.program]
        elif isinstance(self.arguments, str):
            cmd = [self.program, self.arguments]
        else:
            cmd = [self.program, *list(self.arguments)]
        # Prepend sudo if requested
        if self.sudo:
            cmd = ["sudo", *cmd]
        return cmd

    def __repr__(self) -> str:
        return shlex.join(self.argv)


# --- Command runner ---------------------------------------------------------
class CmdRunner:
    def __init__(
        self,
        dry: bool = False,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        self.dry = dry
        self.verbose = verbose
        self.quiet = quiet

    def __call__(
        self, command: Command, exc: Optional[type[Exception]] = None
    ) -> None:
        if self.dry:
            logger.info(f"[dry-run] {command}")
            return
        if not self.quiet:
            logger.info(f"{command}")
        # Always capture output for logging
        result = subprocess.run(command.argv, text=True, capture_output=True)
        out = result.stdout.strip() if result.stdout else ""
        err = result.stderr.strip() if result.stderr else ""
        if self.verbose:
            if out:
                logger.debug(out)
            if err:
                logger.debug(err)
        if result.returncode:
            if exc:
                logger.error(err)
                raise exc(
                    f"{command} failed with exit code {result.returncode}"
                )
            else:
                logger.warning(
                    f"{command} failed with exit code {result.returncode}"
                )
                if err:
                    logger.warning(err)
        elif not self.quiet:
            logger.info(f"{command} completed successfully")


# --- Package manager abstraction --------------------------------------------
class PackageManager:
    def __init__(
        self,
        name: str,
        runner: CmdRunner,
        program: Union[str, Sequence[str]],
        bootstrap_cmd: Command,
        update_args: Optional[
            Union[Sequence[str], Callable[[], Sequence[str]]]
        ],
        install_args: Optional[
            Union[Sequence[str], Callable[[], Sequence[str]]]
        ],
        cleanup_args: Optional[
            Union[Sequence[str], Callable[[], Sequence[str]]]
        ],
        uninstall_args: Optional[
            Union[Sequence[str], Callable[[], Sequence[str]]]
        ] = None,
        use_sudo: bool = False,
    ) -> None:
        self.name = name
        self.runner = runner
        self.program = program
        self.bootstrap_cmd = bootstrap_cmd
        self.use_sudo = use_sudo
        self._update_args = update_args
        self._install_args = install_args
        self._cleanup_args = cleanup_args
        self._uninstall_args = uninstall_args

    @property
    def installed(self) -> bool:
        candidates = (
            [self.program]
            if isinstance(self.program, str)
            else list(self.program)
        )
        return any(shutil.which(c) for c in candidates if isinstance(c, str))

    def _get_args(
        self, args_source: Union[Sequence[str], Callable[[], Sequence[str]]]
    ) -> Sequence[str]:
        return args_source() if callable(args_source) else args_source

    def bootstrap(self) -> None:
        if not self.installed:
            self.runner(self.bootstrap_cmd, BootstrapError)
        else:
            logger.info(f"{self.name} present; skip bootstrap")

    def update(self) -> None:
        if self.installed and self._update_args:
            args = self._get_args(self._update_args)
            cmd = Command(self.program, args, sudo=self.use_sudo)
            self.runner(cmd, UpdateError)

    def install(self) -> None:
        if self.installed and self._install_args:
            args = self._get_args(self._install_args)
            cmd = Command(self.program, args, sudo=self.use_sudo)
            self.runner(cmd, InstallError)
        else:
            logger.info(f"{self.name} not present; skip install")

    def cleanup(self) -> None:
        if self.installed and self._cleanup_args:
            args = self._get_args(self._cleanup_args)
            cmd = Command(self.program, args, sudo=self.use_sudo)
            self.runner(cmd, CleanupError)

    def uninstall(self) -> None:
        if self.installed and self._uninstall_args:
            args = self._get_args(self._uninstall_args)
            cmd = Command(self.program, args, sudo=self.use_sudo)
            self.runner(cmd, UninstallError)

    def __call__(self) -> None:
        self.bootstrap()
        self.update()
        self.install()
        self.cleanup()


# --- Factory functions ------------------------------------------------------
def create_managers(runner: CmdRunner) -> Sequence[PackageManager]:
    # Setup apt
    aptfile = PACKAGE_LIST / "aptfile"
    if not aptfile.exists():
        raise InstallError(f"cannot find {aptfile} for apt")
    packages = [
        ln.strip()
        for ln in aptfile.read_text().splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    apt = PackageManager(
        name="apt",
        runner=runner,
        program="/usr/bin/apt",
        bootstrap_cmd=Command("true"),
        update_args=["update"],
        install_args=["install", "-y", *packages],
        cleanup_args=["autoremove", "-y"],
        use_sudo=True,
    )

    brewfile = PACKAGE_LIST / "Brewfile"
    if not brewfile.exists():
        raise InstallError(f"cannot find {brewfile} for Homebrew")
    brew = PackageManager(
        name="brew",
        runner=runner,
        program=[
            "brew",
            "/opt/homebrew/bin/brew",
            "/usr/local/Homebrew/bin/brew",
            "/home/linuxbrew/.linuxbrew/bin/brew",
        ],
        bootstrap_cmd=Command(
            "sh",
            [
                "-c",
                "NONINTERACTIVE=1 curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh | bash",
            ],
        ),
        update_args=["update"],
        install_args=[
            "bundle",
            "--file",
            str(brewfile),
            "--cleanup",
            "--force",
            "--quiet",
        ],
        cleanup_args=["cleanup"],
    )

    cargo_tools = PACKAGE_LIST / "cargofile"
    if not cargo_tools.exists():
        raise InstallError(f"cannot find {cargo_tools} for cargo")
    cargo_tools = [
        ln.strip()
        for ln in cargo_tools.read_text().splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    cargo = PackageManager(
        name="cargo",
        runner=runner,
        program=f"{HOME}/.cargo/bin/cargo",
        bootstrap_cmd=Command(
            "sh",
            [
                "-c",
                'curl --proto "=https" --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y',
            ],
        ),
        update_args=["install-update", "-a"],
        install_args=["install", *cargo_tools],
        cleanup_args=None,
    )

    pip_tools = PACKAGE_LIST / "pipfile"
    if not pip_tools.exists():
        raise InstallError(f"cannot find {pip_tools} for uv")
    uv = PackageManager(
        name="uv",
        runner=runner,
        program="uv",
        bootstrap_cmd=Command(
            "sh",
            [
                "-c",
                "curl -LsSf https://astral.sh/uv/install.sh | sh",
            ],
        ),
        update_args=["upgrade"],
        install_args=["tool", "install", "--with-requirements", f"{pip_tools}"],
        cleanup_args=None,
    )
    return [apt, brew, cargo, uv]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="update.py")
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Test commands without running",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"{parser.prog} {__version__}",
    )
    verbosity = parser.add_mutually_exclusive_group(required=False)
    verbosity.add_argument(
        "-v", "--verbose", action="store_true", help="Print all log messages"
    )
    verbosity.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Supress messages (besides errors)",
    )

    args = parser.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    elif args.quiet:
        logging.getLogger().setLevel(logging.ERROR)

    runner = CmdRunner(dry=args.dry_run, verbose=args.verbose, quiet=args.quiet)
    for manager in create_managers(runner):
        manager()
