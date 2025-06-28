#!/usr/bin/env python

import argparse
import logging
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Optional, Sequence, Union

__version__ = "0.1.0"

parser = argparse.ArgumentParser("packages.py")
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
    "-v", "--verbose", action="store_true", help=" print diagnostic messages"
)
verbosity.add_argument(
    "-q", "--quiet", action="store_true", help="supress non-error messages"
)
other = parser.add_subparsers(title="test")
logger = logging.Logger(name=parser.prog)


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


@dataclass
class PackageManager:
    name: str
    command: Command
    selfinstall_args: Optional[Union[str, Sequence[str]]]
    updateself_args: Optional[Union[str, Sequence[str]]]
    updateprogs_args: Optional[Union[str, Sequence[str]]]
    installprogs_args: Optional[Union[str, Sequence[str]]]
    parser: argparse.ArgumentParser = field(init=False)

    def __post_init__(self) -> None:
        self.parser = other.add_parser(
            name=self.name, help=f"manage the {self.name} package manager"
        )
        self.parser.add_argument(
            "action",
            choices=[
                "self_install",
                "self_update",
                "pkg install",
                "pkg update",
                "pkg cleanup",
            ],
            help="testing this",
        )
        # something else


x = PackageManager("apt", Command("brew", ""), "", "", "", "")

args = parser.parse_args()
