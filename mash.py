#!/usr/bin/env python3

import argparse
import functools
import logging
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
from itertools import product
from pathlib import Path
from typing import Dict, Optional, Sequence, Set, Tuple, Union

__NAME__ = "mash.py"
__version__ = "0.1.0"

if platform.system() == "Windows":
    raise RuntimeError(
        f"{__NAME__} {__version__} is not available on windows (yet) it may work in WSL, but this has not been tested"
    )
if sys.version_info < (3, 9):
    raise RuntimeError(f"{__NAME__} {__version__} requires Python >= 3.9")


EXTRA_DIRECTIVES = ["shell", "apt", "cargo", "uv", "stow"]
BREW_IMPORTANCE = 2
ALL_DIRECTIVES = (
    EXTRA_DIRECTIVES[:BREW_IMPORTANCE]
    + ["brew"]
    + EXTRA_DIRECTIVES[BREW_IMPORTANCE:]
)

IS_MACOS = platform.system() == "Darwin"
IS_ARM = platform.processor() == "arm"

DANGEROUS_SHELL_PATTERNS = [
    re.compile(r"\brm\s+-rf\b"),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bshutdown\b"),
]
LONG_OVERRIDE_VAR = (
    "IAMOKAYWITHMASHDOTPYEXECUTINGARBITRARYSHELLCOMMANDS"
    "ANDIKNOWTHEMANYMANYRISKSOFLETTINGASCRIPTDOTHISNONSENSE"
)
SIMPLE_OVERRIDE_VAR = "MASH_EXEC_UNSAFE"


def clean_lines(lines: Sequence[str]) -> Sequence[str]:
    return [
        a
        for a in [
            re.compile(r"\s*#.*$").sub("", b).strip()
            for b in [re.compile(r"[\r\n]").sub("", c) for c in lines]
        ]
        if a
    ]


def extra_directive(command: str) -> Tuple[str, re.Pattern[str]]:
    return command, re.compile(rf"^\s*{command}\s+['\"].*['\"]$")


def match_and_doesnt(
    lines: Sequence[str], pattern: re.Pattern[str]
) -> Tuple[Sequence[str], Sequence[str]]:
    matches = [line for line in lines if pattern.match(line)]
    doesnt = [line for line in lines if not pattern.match(line)]
    return matches, doesnt


def read_brewfile(
    file: Path, directives: Sequence[str]
) -> Dict[str, Sequence[str]]:
    with file.open("rt") as conn:
        lines = clean_lines(conn.readlines())

    # Progressive filtering
    processed: Dict[str, Sequence[str]] = {}
    for label, pattern in [
        extra_directive(directive) for directive in directives
    ]:
        matched, lines = match_and_doesnt(lines, pattern)
        processed[label] = matched

    processed["brew"] = lines  # Remaining lines assumed to be brew directives
    return processed


def order_importance(
    file: Path,
) -> Sequence[Tuple[str, Sequence[str]]]:
    directives_values = read_brewfile(file, EXTRA_DIRECTIVES)
    if IS_MACOS:
        del directives_values["apt"]
    elif IS_ARM:
        # TODO: Implement something to allow build from source and remove the
        #       casks. Ideally, the brew commands will include stuff for linux
        #       as well.
        #       For now, this is no-op
        pass
    if extra := set(directives_values.keys()).difference(ALL_DIRECTIVES):
        raise ValueError(f"additional directives {extra} not allowed")
    out: Sequence[Tuple[str, Sequence[str]]] = []
    for directive in ALL_DIRECTIVES:
        current_values = directives_values.get(directive)
        if current_values is not None:
            out.append((directive, current_values))
    return out


class Command:
    def __init__(
        self,
        program: Union[str, Sequence[str]],
        arguments: Optional[Union[str, Sequence[str]]] = None,
        sudo: bool = False,
        env: Optional[Dict[str, str]] = None,
        stdout: Optional[int] = subprocess.PIPE,
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
        self.env = env
        self.stdout = stdout
        for command, pattern in product(self.argv, DANGEROUS_SHELL_PATTERNS):
            if pattern.search(command):
                raise RuntimeError(
                    f"refuse to run dangerous command {self.argv}"
                )

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


class CmdRunner:
    def __init__(
        self,
        logger: logging.Logger,
        dry: bool = False,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        self.dry = dry
        self.verbose = verbose
        self.quiet = quiet
        self.logger = logger

    def __call__(
        self, command: Command, exc: Optional[type[Exception]] = None
    ) -> None:
        if self.dry:
            self.logger.info(f"[dry-run] {command}")
            return
        if not self.quiet:
            self.logger.info(f"{command}")
        # Always capture output for logging
        result = subprocess.run(
            command.argv,
            text=True,
            capture_output=True if command.stdout is None else False,
            stdout=command.stdout,
            env=command.env,
        )
        out = result.stdout.strip() if result.stdout else ""
        err = result.stderr.strip() if result.stderr else ""
        if self.verbose:
            if out:
                self.logger.debug(out)
            if err:
                self.logger.debug(err)
        if result.returncode:
            if exc:
                self.logger.error(err)
                raise exc(
                    f"{command} failed with exit code {result.returncode}"
                )
            else:
                self.logger.warning(
                    f"{command} failed with exit code {result.returncode}"
                )
                if err:
                    self.logger.warning(err)
        elif not self.quiet:
            self.logger.info(f"{command} completed successfully")


@functools.lru_cache(maxsize=1)
def ensure_curl_available(runner: CmdRunner) -> bool:
    import shutil

    if shutil.which("curl") is None:
        runner.logger.error("curl is required but not found in PATH.")
        return False
    return True


def get_brewfile(proposed_path: Optional[str]) -> Path:
    brewfile_location = (
        proposed_path
        if proposed_path
        else os.environ.get(
            "BREWFILE_PATH", os.environ.get("MASHFILE_PATH", "./Brewfile")
        )
    )
    brewfile = Path(brewfile_location).expanduser().resolve()
    if not brewfile.exists():
        raise FileNotFoundError(
            f"Could not locate Brewfile. Tried: CLI argument {proposed_path or '(none)'}, "
            "$BREWFILE_PATH, $MASHFILE_PATH, and ./Brewfile"
        )

    return brewfile


def handle_shell(args: Sequence[str], runner: CmdRunner, unsafe: bool) -> None:
    shell_cmds = "\n\t" + "\n\t".join(args)

    # Check environment variables for override
    env_override = (
        os.environ.get(LONG_OVERRIDE_VAR) is not None
        or os.environ.get(SIMPLE_OVERRIDE_VAR) == "1"
    )

    if env_override:
        logger.info(f"running commands: {shell_cmds}")
    elif unsafe:
        logger.warning(
            f"are you certain you wish to run commands:{shell_cmds}"
            "\n[y]es/[n]o\n>"
        )
        if input("").lower() != "y":
            logger.warning("aborted by user")
            return
    else:
        logger.warning(
            f"did not run commands:{shell_cmds}"
            + "\n\nto run these commands, use the -u/--unsafe flag"
        )
        return

    for line in args:
        match = re.match(r'^\s*shell\s+[\'"](.+)[\'"]$', line)
        if not match:
            runner.logger.warning(f"Unrecognized shell directive: {line}")
            continue

        command_str = match.group(1)
        cmd = Command(program="/bin/sh", arguments=["-c", command_str])
        runner(cmd, exc=RuntimeError)


def handle_apt(args: Sequence[str], runner: CmdRunner) -> None:
    import subprocess

    # Extract package names from lines like: apt "pkgname"
    apt_packages: Sequence[str] = []
    for line in args:
        match = re.match(r'^\s*apt\s+[\'"](.*)[\'"]$', line)
        if not match:
            runner.logger.warning(f"Unrecognized apt directive: {line}")
            continue
        apt_packages.append(match.group(1))

    if not apt_packages:
        runner.logger.info("No apt packages to install.")
        return

    # Update and upgrade the package lists
    runner(Command("apt", ["update"], sudo=True))
    runner(Command("apt", ["upgrade", "-y"], sudo=True))

    # Install required packages
    runner(Command("apt", ["install", "-y"] + apt_packages, sudo=True))

    # Get current manually installed packages
    try:
        result = subprocess.run(
            ["apt-mark", "showmanual"],
            capture_output=True,
            text=True,
            check=True,
        )
        manually_installed = set(result.stdout.strip().splitlines())
    except subprocess.CalledProcessError as e:
        runner.logger.error("Failed to retrieve manually installed packages.")
        runner.logger.debug(f"{e}")
        return

    # Determine which manual packages should be removed
    to_remove = sorted(manually_installed - set(apt_packages))
    if to_remove:
        runner.logger.info(
            "Removing packages no longer listed in the Brewfile:"
        )
        for pkg in to_remove:
            runner.logger.info(f"  {pkg}")
        runner(
            Command("apt", ["remove", "--purge", "-y"] + to_remove, sudo=True)
        )

    # Autoremove and clean unused dependencies
    runner(Command("apt", ["autoremove", "-y"], sudo=True))
    runner(Command("apt", ["autoclean"], sudo=True))


def handle_brew(args: Sequence[str], runner: CmdRunner) -> None:
    import tempfile

    # Resolve brew path using environment variables or standard defaults
    brew_env = os.environ.get("HOMEBREW_PREFIX")
    brew_path = shutil.which("brew")

    if not brew_path:
        if not brew_env:
            if IS_MACOS:
                brew_env = "/opt/homebrew" if IS_ARM else "/usr/local"
            else:
                brew_env = str(Path.home() / ".linuxbrew")
        brew_path = str(Path(brew_env) / "bin" / "brew")

    if not Path(brew_path).is_file():
        if not (ensure_curl_available(runner) and shutil.which("git")):
            runner.logger.warning(
                "Homebrew requires both curl and git to install."
            )
            return

        runner.logger.info("Installing Homebrew...")
        runner(
            Command(
                "/bin/bash",
                [
                    "-c",
                    "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)",
                ],
            )
        )

    if not Path(brew_path).is_file():
        runner.logger.error("brew not found after attempted installation.")
        return

    # Update and upgrade brew
    runner(Command(brew_path, ["update"]))
    runner(Command(brew_path, ["upgrade"]))

    # Write Brewfile contents to a temp file and bundle install
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as temp:
        for line in args:
            temp.write(line + "")
        temp_path = temp.name

    runner(Command(brew_path, ["bundle", "--file", temp_path]))

    # Cleanup unused packages
    runner(Command(brew_path, ["cleanup"]))
    os.remove(temp_path)


def handle_cargo(args: Sequence[str], runner: CmdRunner) -> None:
    import os
    import shutil
    import subprocess

    prefix = os.environ.get("CARGO_HOME", str(Path.home() / ".cargo"))
    bin_dir = Path(prefix) / "bin"
    cargo_path = shutil.which("cargo") or str(bin_dir / "cargo")
    rustup_path = shutil.which("rustup") or str(bin_dir / "rustup")

    if not Path(rustup_path).is_file():
        if not ensure_curl_available(runner):
            runner.logger.warning(
                "rustup/cargo is not present and it requires curl to install"
            )
            return
        runner.logger.info("Installing rustup...")
        runner(
            Command(
                "curl",
                [
                    "--proto",
                    "'=https'",
                    "--tlsv1.2",
                    "-sSf",
                    "https://sh.rustup.rs",
                ],
                stdout=None,
            )
        )
        runner(
            Command(
                "sh",
                ["-s", "--", "-y"],
                env={
                    "CARGO_HOME": prefix,
                    "RUSTUP_HOME": os.environ.get("RUSTUP_HOME", prefix),
                },
            )
        )

    if not Path(cargo_path).is_file() or not Path(rustup_path).is_file():
        runner.logger.error(
            "Cargo or rustup not found even after installation."
        )
        return

    runner(Command(rustup_path, ["self", "update"]))
    runner(Command(cargo_path, ["install-update", "-a"]))

    cargo_packages: Sequence[str] = []
    for line in args:
        match = re.match(r'^\s*cargo\s+[\'"](.*)[\'"]$', line)
        if not match:
            runner.logger.warning(f"Unrecognized cargo directive: {line}")
            continue
        cargo_packages.append(match.group(1))

    if not cargo_packages:
        runner.logger.info("No cargo packages to install.")
        return

    for pkg in cargo_packages:
        runner(Command(cargo_path, ["install", pkg]))

    try:
        result = subprocess.run(
            [cargo_path, "install", "--list"],
            capture_output=True,
            text=True,
            check=True,
        )
        installed: Set[str] = set()
        for line in result.stdout.strip().splitlines():
            if line.strip() and " " in line:
                pkg = line.split()[0]
                installed.add(pkg)
    except subprocess.CalledProcessError:
        runner.logger.error("Failed to retrieve installed cargo packages.")
        return

    to_remove = sorted(installed - set(cargo_packages))
    if to_remove:
        runner.logger.info(
            "Removing cargo packages no longer listed in the Brewfile:"
        )
        for pkg in to_remove:
            runner.logger.info(f"  {pkg}")
            runner(Command(cargo_path, ["uninstall", pkg]))


def handle_uv(args: Sequence[str], runner: CmdRunner) -> None:
    prefix = os.environ.get("UV_HOME", str(Path.home() / ".local/uv"))
    bin_dir = Path(prefix) / "bin"
    uv_path = shutil.which("uv") or str(bin_dir / "uv")
    uv_installed = Path(uv_path).is_file()

    if not uv_installed:
        if not ensure_curl_available(runner):
            runner.logger.warning(
                "uv is not present and it requires curl to install"
            )
            return
        runner.logger.info("Installing uv using upstream method...")
        runner(
            Command(
                "curl",
                ["-LsSf", "https://astral.sh/uv/install.sh"],
                stdout=None,
            )
        )
        runner(Command("sh", ["-s"], env={"UV_HOME": prefix}))

    if not Path(uv_path).is_file():
        runner.logger.error("uv not found after attempted installation.")
        return
    elif not uv_installed:  # Things to do after first install
        pass  # TODO: symlink python and python3 from uv

    runner(Command(uv_path, ["self", "update"]))

    uv_packages: Sequence[str] = []
    for line in args:
        match = re.match(r'^\s*uv\s+[\'"](.*)[\'"]$', line)
        if not match:
            runner.logger.warning(f"Unrecognized uv directive: {line}")
            continue
        uv_packages.append(match.group(1))

    if not uv_packages:
        runner.logger.info("No uv packages to install.")
        return

    runner(
        Command(uv_path, ["pip", "install"] + uv_packages)
    )  # NOTE: check for official installation methods for tools

    try:
        result = subprocess.run(
            [uv_path, "pip", "freeze"],
            capture_output=True,
            text=True,
            check=True,
        )
        installed: Set[str] = set()
        for line in result.stdout.strip().splitlines():
            pkg = line.split("==")[0]
            installed.add(pkg)
    except subprocess.CalledProcessError:
        runner.logger.error("Failed to retrieve installed uv packages.")
        return

    to_remove = sorted(installed - set(uv_packages))
    if to_remove:
        runner.logger.info(
            "Removing uv packages no longer listed in the Brewfile:"
        )
        runner(Command(uv_path, ["pip", "uninstall", "-y"] + to_remove))


def handle_stow(args: Sequence[str], runner: CmdRunner) -> None:
    stow_path = shutil.which("stow")
    if not stow_path:
        runner.logger.warning(
            "GNU stow is not installed; skipping stow directives."
        )
        return

    home = Path.home()
    valid_dirs: Sequence[Path] = []

    for line in args:
        match = re.match(r'^\s*stow\s+[\'"](.*)[\'"]$', line)
        if not match:
            runner.logger.warning(f"Unrecognized stow directive: {line}")
            continue

        stow_target = Path(match.group(1)).expanduser()
        if not stow_target.is_absolute():
            stow_target = home / stow_target

        if not stow_target.exists():
            runner.logger.warning(f"Directory not found: {stow_target}")
            continue

        valid_dirs.append(stow_target)

    if not valid_dirs:
        runner.logger.info("No valid stow directories to process.")
        return

    # First unstow everything to ensure a clean resymlink
    for target in valid_dirs:
        runner(
            Command(
                stow_path,
                [
                    "--dir",
                    str(target.parent),
                    "--target",
                    str(home),
                    "--delete",
                    target.name,
                ],
            )
        )

    # Then restow each directory
    for target in valid_dirs:
        runner(
            Command(
                stow_path,
                [
                    "--dir",
                    str(target.parent),
                    "--target",
                    str(home),
                    target.name,
                ],
            )
        )


def main(brewfile: Path, runner: CmdRunner, unsafe: bool = False) -> None:
    for action, args in order_importance(brewfile):
        runner.logger.info(f"running {action}-based commands")
        if action == "shell":
            handle_shell(args, runner, unsafe)
        elif action == "apt":
            handle_apt(args, runner)
        elif action == "brew":
            handle_brew(args, runner)
            pass
        elif action == "cargo":
            handle_cargo(args, runner)
            pass
        elif action == "uv":
            handle_uv(args, runner)
            pass
        elif action == "stow":
            handle_stow(args, runner)
        else:
            # Code is theoretically unreachable
            break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog=__NAME__,
        description="Preprocess 'Brewfile' to allow packaging with apt, uv, cargo, stowwing dotfiles with GNU stow, and executing commands through /bin/sh",
        epilog="If no brewfile is specified, the environment variables BREWFILE_PATH and MASHFILE_PATH will be used if defined",
    )

    # Standard flags
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"{parser.prog} {__version__}",
        help="print version information and exit",
    )
    parser.add_argument(
        "-n", "--dry-run", action="store_true", help="dry run of installation"
    )
    verbosity = parser.add_mutually_exclusive_group(required=False)
    verbosity.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help=" print diagnostic messages",
    )
    verbosity.add_argument(
        "-q", "--quiet", action="store_true", help="supress non-error messages"
    )

    # Only positional argument
    parser.add_argument(
        "brewfile",
        nargs="?",
        default="",
        help="local or absolute path to Brewfile",
    )

    parser.add_argument(
        "-u",
        "--unsafe",
        action="store_true",
        help="allow execution of shell commands via `shell` directive",
    )

    # Set main variables
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )
    logger = logging.getLogger(__NAME__)
    runner = CmdRunner(
        logger, dry=args.dry_run, verbose=args.verbose, quiet=args.quiet
    )

    brewfile = get_brewfile(args.brewfile)
    main(brewfile, runner, unsafe=args.unsafe)
