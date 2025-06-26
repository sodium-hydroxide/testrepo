#!/usr/bin/env python3
__version__ = "0.1.0"

# %% Imports
import argparse
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Callable, ClassVar, Optional, Sequence, Union

if sys.version_info < (3, 9):  # Enforce Python version
    raise RuntimeError("Python >= 3.9 required")

# %% Logger setup
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")

# %% Type aliases & constants
Command = Union[str, Sequence[str]]


# %% Exit codes
class ExitCode(IntEnum):
    # --- General Status Codes ---
    OK = 0
    GENERIC_ERROR = 1
    CONFIG_ERROR = 2
    MISSING_DEPENDENCY = 3
    INTERRUPTED = 4
    PERMISSION_DENIED = 5
    INVALID_ARGUMENTS = 6
    UNSUPPORTED_PLATFORM = 7
    # Codes 8-10 reserved
    # --- Dotfiles / Stow ---
    DOTFILES_MISSING = 11
    DOTFILES_STOW_FAILED = 12
    # Codes 12-20 reserved
    # --- APT (21–29) ---
    APT_BOOTSTRAP_FAILED = 21  # should never occur
    APT_UPDATE_FAILED = 22
    APT_INSTALL_FAILED = 23
    APT_CLEANUP_FAILED = 24
    APT_UNINSTALL_FAILED = 25  # should never occur
    # Codes 26-30 reserved
    # --- Homebrew (31–39) ---
    BREW_BOOTSTRAP_FAILED = 31
    BREW_UPDATE_FAILED = 32
    BREW_INSTALL_FAILED = 33
    BREW_CLEANUP_FAILED = 34
    BREW_UNINSTALL_FAILED = 35
    # Codes 36-40 reserved
    # --- Cargo (41–49) ---
    CARGO_BOOTSTRAP_FAILED = 41
    CARGO_UPDATE_FAILED = 42
    CARGO_INSTALL_FAILED = 43
    CARGO_CLEANUP_FAILED = 44
    CARGO_UNINSTALL_FAILED = 45
    # Codes 46-50 reserved
    # --- uv (51–59) ---
    UV_BOOTSTRAP_FAILED = 51
    UV_UPDATE_FAILED = 52
    UV_INSTALL_FAILED = 53
    UV_CLEANUP_FAILED = 54
    UV_UNINSTALL_FAILED = 55
    # Codes 56-60 reserved
    # Codes 61-89 Reserved
    # --- Misc (90–99) ---
    UNKNOWN_PACKAGE_MANAGER = 90
    SYSTEM_CHECK_FAILED = 91
    MANAGER_EXEC_FAILED = 92
    TOML_PARSE_FAILED = 93
    FILE_IO_ERROR = 94
    # Codes 95-127 Reserved


@dataclass(frozen=True)
class ExitCodeMap:
    bootstrap_failure: ExitCode
    update_failure: ExitCode
    install_failure: ExitCode
    uninstall_failure: ExitCode
    cleanup_failure: ExitCode


# %% CmdRunner for injection


class CmdRunner:
    def __init__(
        self, dry: bool = False, verbose: bool = False, quiet: bool = False
    ) -> None:
        self.dry = dry
        self.verbose = verbose
        self.quiet = quiet

    def run(
        self, cmd: Command, desc: Optional[str], on_error: ExitCode
    ) -> subprocess.CompletedProcess:
        label = desc or (cmd if isinstance(cmd, str) else " ".join(cmd))
        if self.dry:
            logger.info(f"[dry-run] {label}")
            # simulate success
            return subprocess.CompletedProcess(args=cmd, returncode=0)

        logger.info(f"{label}...")
        if isinstance(cmd, str):
            proc = subprocess.run(
                cmd, shell=True, text=True, capture_output=not self.verbose
            )
        else:
            proc = subprocess.run(
                cmd, shell=False, text=True, capture_output=not self.verbose
            )

        if proc.returncode == 0:
            logger.info(f"{label}")
        else:
            logger.error(f"{label} failed: {proc.stderr.strip()}")
            sys.exit(on_error)
        return proc


# %% Core classes
@dataclass(repr=False, frozen=True)
class PackageManager:
    name: str
    dependency_file: Path
    exit_codes: ExitCodeMap
    install_func: Callable[[Path], ExitCode]
    runner: CmdRunner
    bootstrap_cmd: Optional[Command] = None
    update_cmd: Optional[Command] = None
    cleanup_cmd: Optional[Command] = None
    uninstall_cmd: Optional[Command] = None

    @classmethod
    def instances(cls) -> list["PackageManager"]:
        return list(cls._registry)

    def _run(
        self, cmd: Optional[Command], desc: str, code: ExitCode
    ) -> ExitCode:
        if cmd is None:
            return ExitCode.OK
        self.runner.run(cmd, desc, code)
        return ExitCode.OK

    def bootstrap(self) -> ExitCode:
        if shutil.which(self.name):
            logger.info(f"{self.name} already installed, skipping")
            return ExitCode.OK
        return self._run(
            self.bootstrap_cmd,
            f"bootstrap {self.name}",
            self.exit_codes.bootstrap_failure,
        )

    def update(self) -> ExitCode:
        return self._run(
            self.update_cmd,
            f"update {self.name}",
            self.exit_codes.update_failure,
        )

    def cleanup(self) -> ExitCode:
        return self._run(
            self.cleanup_cmd,
            f"cleanup {self.name}",
            self.exit_codes.cleanup_failure,
        )

    def uninstall(self) -> ExitCode:
        return self._run(
            self.uninstall_cmd,
            f"uninstall {self.name}",
            self.exit_codes.uninstall_failure,
        )

    def install_dependencies(self) -> ExitCode:
        return self.install_func(self.dependency_file)

    def __call__(self) -> ExitCode:
        for action in (
            self.bootstrap,
            self.update,
            self.install_dependencies,
            self.cleanup,
        ):
            result = action()
            if result != ExitCode.OK:
                return result
        return ExitCode.OK


@dataclass()
class DotfilesManager:
    dotfiles_directory: Path
    stow_ignore: Sequence[str]
    runner: CmdRunner

    def get_stow_list(self) -> list[str]:
        if not self.dotfiles_directory.is_dir():
            return []
        return sorted(
            item.name
            for item in self.dotfiles_directory.iterdir()
            if item.is_dir()
            and not item.name.startswith(".")
            and item.name not in self.stow_ignore
        )

    def sync(self) -> ExitCode:
        if shutil.which("stow") is None:
            logger.error("'stow' not found: required for syncing dotfiles")
            return ExitCode.MISSING_DEPENDENCY

        to_stow = self.get_stow_list()
        if not to_stow:
            logger.info("No dotfiles to stow")
            return ExitCode.DOTFILES_MISSING
        self.runner.run(
            [
                "stow",
                "--restow",
                "--verbose",
                f"--dir={self.dotfiles_directory}",
                f"--target={Path.home()}",
                *to_stow,
            ],
            "restow dotfiles",
            ExitCode.DOTFILES_STOW_FAILED,
        )
        return ExitCode.OK


# %% Concrete manager factories


def make_managers(runner: CmdRunner) -> list[PackageManager]:
    # Homebrew
    def _brew_install(brewfile: Path) -> ExitCode:
        if not brewfile.exists():
            logger.info(f"No Brewfile at {brewfile}, skipping")
            return ExitCode.OK
        runner.run(
            [
                "brew",
                "bundle",
                "--file",
                str(brewfile),
                "--cleanup",
                "--force",
                "--quiet",
            ],
            f"brew bundle ({brewfile.name})",
            ExitCode.BREW_INSTALL_FAILED,
        )
        return ExitCode.OK

    # APT
    def _apt_install(pkgfile: Path) -> ExitCode:
        if not pkgfile.exists():
            logger.info(f"No apt package list at {pkgfile}, skipping")
            return ExitCode.OK
        pkgs = [
            ln.strip()
            for ln in pkgfile.read_text().splitlines()
            if ln.strip() and not ln.startswith("#")
        ]
        if not pkgs:
            logger.info("No apt packages listed, skipping")
            return ExitCode.OK
        runner.run(
            ["sudo", "apt", "install", "-y", *pkgs],
            "apt install packages",
            ExitCode.APT_INSTALL_FAILED,
        )
        return ExitCode.OK

    # Cargo
    def _cargo_install(toolfile: Path) -> ExitCode:
        if not toolfile.exists():
            logger.info(f"No cargo tools file at {toolfile}, skipping")
            return ExitCode.OK
        tools = [
            ln.strip()
            for ln in toolfile.read_text().splitlines()
            if ln.strip() and not ln.startswith("#")
        ]
        if not tools:
            logger.info("No cargo tools listed, skipping")
            return ExitCode.OK
        for tool in tools:
            runner.run(
                ["cargo", "install", tool],
                f"cargo install {tool}",
                ExitCode.CARGO_INSTALL_FAILED,
            )
        return ExitCode.OK

    # UV
    def _uv_install(pyproject: Path) -> ExitCode:
        if not pyproject.exists():
            logger.info(f"No pyproject.toml at {pyproject}, skipping")
            return ExitCode.OK
        runner.run(
            ["uv", "pip", "install", "--system", "--upgrade"],
            "uv install system tools",
            ExitCode.UV_INSTALL_FAILED,
        )
        return ExitCode.OK

    # Instantiate managers
    managers: list[PackageManager] = []
    managers.append(  # Homebrew
        PackageManager(
            name="brew",
            dependency_file=Path.home() / "dotfiles" / "package-list" / "Brewfile",
            exit_codes=ExitCodeMap(
                bootstrap_failure=ExitCode.BREW_BOOTSTRAP_FAILED,
                update_failure=ExitCode.BREW_UPDATE_FAILED,
                install_failure=ExitCode.BREW_INSTALL_FAILED,
                uninstall_failure=ExitCode.MANAGER_EXEC_FAILED,
                cleanup_failure=ExitCode.BREW_CLEANUP_FAILED,
            ),
            install_func=_brew_install,
            runner=runner,
            bootstrap_cmd="/bin/bash -c 'NONINTERACTIVE=1 curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh | bash'",
            update_cmd=["brew", "update"],
            cleanup_cmd=["brew", "cleanup"],
        )
    )
    managers.append(  # apt - Builtin
        PackageManager(
            name="apt",
            dependency_file=Path.home() / "dotfiles" / "package-list" / "aptfile",
            exit_codes=ExitCodeMap(
                bootstrap_failure=ExitCode.APT_BOOTSTRAP_FAILED,
                update_failure=ExitCode.APT_UPDATE_FAILED,
                install_failure=ExitCode.APT_INSTALL_FAILED,
                uninstall_failure=ExitCode.MANAGER_EXEC_FAILED,
                cleanup_failure=ExitCode.APT_CLEANUP_FAILED,
            ),
            install_func=_apt_install,
            runner=runner,
            bootstrap_cmd=None,
            update_cmd=["sudo", "apt", "update"],
            cleanup_cmd=["sudo", "apt", "autoremove", "-y"],
        )
    )
    # managers.append(  # cargo - rust
    #     PackageManager(
    #         name="cargo",
    #         dependency_file=Path.home() / "cargo-tools.txt",
    #         exit_codes=ExitCodeMap(
    #             bootstrap_failure=ExitCode.CARGO_BOOTSTRAP_FAILED,
    #             update_failure=ExitCode.CARGO_UPDATE_FAILED,
    #             install_failure=ExitCode.CARGO_INSTALL_FAILED,
    #             uninstall_failure=ExitCode.MANAGER_EXEC_FAILED,
    #             cleanup_failure=ExitCode.CARGO_CLEANUP_FAILED,
    #         ),
    #         install_func=_cargo_install,
    #         runner=runner,
    #         bootstrap_cmd="sh -c 'curl --proto \"=https\" --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y'",
    #         update_cmd=["cargo", "install-update", "-a"],
    #     )
    # )
    # managers.append(  # uv - python
    #     PackageManager(
    #         name="uv",
    #         dependency_file=Path.home() / "pyproject.toml",
    #         exit_codes=ExitCodeMap(
    #             bootstrap_failure=ExitCode.UV_BOOTSTRAP_FAILED,
    #             update_failure=ExitCode.UV_UPDATE_FAILED,
    #             install_failure=ExitCode.UV_INSTALL_FAILED,
    #             uninstall_failure=ExitCode.MANAGER_EXEC_FAILED,
    #             cleanup_failure=ExitCode.UV_CLEANUP_FAILED,
    #         ),
    #         install_func=_uv_install,
    #         runner=runner,
    #         bootstrap_cmd=["cargo", "install", "uv"],
    #         update_cmd=["uv", "pip", "install", "--system", "--upgrade"],
    #     )
    # )
    return managers


# %% CLI
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="update.py",
        description="Bootstrap system utilities",
    )
    parser.add_argument(
        "-e", "--edit", action="store_true", help="Edit dotfiles repo and exit."
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Dry run mode: show commands without executing.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Verbose command output."
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="Suppress non-error output."
    )
    parser.add_argument(
        "-V", "--version", action="version", version=__version__
    )
    args = parser.parse_args()

    # Create runner
    runner = CmdRunner(dry=args.dry_run, verbose=args.verbose, quiet=args.quiet)
    
    # Set Constants
    DOTFILES = Path.home() / "dotfiles"
    PACKAGES = DOTFILES / "package-list"
    
    
    # Edit mode
    if args.edit:
        editor_cmd = os.environ.get("EDITOR", "vi").split()
        if not shutil.which(editor_cmd[0]):
            logger.error(f"'{editor_cmd}' not found")
            sys.exit(ExitCode.MISSING_DEPENDENCY)
        subprocess.run(editor_cmd + [str(DOTFILES)])
        sys.exit(ExitCode.OK)

    # Run package managers
    managers = make_managers(runner)
    for mgr in managers:
        rc = mgr()
        if rc != ExitCode.OK:
            sys.exit(rc)

    # # Sync dotfiles
    # dot_mgr = DotfilesManager(DOTFILES, ["scripts"], runner)
    # rc = dot_mgr.sync()
    # if rc != ExitCode.OK:
    #     sys.exit(rc)

    sys.exit(ExitCode.OK)
else:
    raise ImportError("sysup is only meant for use as a command line utility")


print("\b" * 7 + " " * 7 + "\b" * 7, end="", flush=True)
