#!/bin/bash
# Standard Environment Variables
export BASH_SILENCE_DEPRECATION_WARNING=1
export SHELL="$LOGIN_SHELL"
export EDITOR="nvim"
export PAGER="bat"
export BROWSER=""
export LANG="en_US.UTF-8"
export HISTSIZE=10000
export HISTFILESIZE=20000
export HISTCONTROL=ignoreboth:erasedups
export CLICOLOR=1
export LSCOLORS=GxFxCxDxBxegedabagaced
export TERM=xterm-256color
export COLORTERM=truecolor
export ICLOUDDRIVE="$HOME/Library/Mobile Documents/com~apple~CloudDocs"

# Git Environment Variables
export GIT_CONFIG_GLOBAL="$XDG_CONFIG_HOME/git/config"

# MCNP Environment Variables
export MCNP_HOME="$HOME/.MCNP/mcnp63/exec"
export DATAPATH="$MCNP_HOME/MCNP_DATA"
export XSDIR="${DATAPATH}/xsdir"
export MCNP_CLUSTER="njblair@129.82.20.78"

# Python Environment Variables
export VIRTUAL_ENV_DISABLE_PROMPT=1
export PYTHONPATH="$HOME/.local/lib/python3/site-packages:$PYTHONPATH"
LDFLAGS="-L$(/opt/homebrew/bin/brew --prefix tcl-tk)/lib"      # tkinter
export LDFLAGS
CPPFLAGS="-I$(/opt/homebrew/bin/brew --prefix tcl-tk)/include" # tkinter
export CPPFLAGS
TCL_LIBRARY=$(/opt/homebrew/bin/brew --prefix tcl-tk)/lib/tcl8.6
export TCL_LIBRARY

# R Environment Variables
export R_HOME="/Library/Frameworks/R.framework/Resources"

# Setting PATH Variable
# Set cryptex bootstrap path
codex_bootstrap="/var/run/com.apple.security.cryptexd/codex.system/bootstrap/usr"

# List of PATH components to add
declare -a path_candidates=(
    # Development tools
    "/usr/local/opt/llvm/bin"
    "$HOME/.local/bin"
    "$HOME/.local/.venv/bin"
    "$HOME/.cargo/bin"

    # MCNP paths
    "$HOME/.MCNP/mcnparse/bin"
    "$MCNP_HOME/mcnp-6.3.0-Darwin/bin"
    "$MCNP_HOME/mcnp-6.3.0-Qt-preview-Darwin/bin"

    # Homebrew core
    "/opt/homebrew/bin"
    "/opt/homebrew/sbin"

    # Traditional system paths
    "/usr/local/bin"
    "/usr/local/sbin"
    "/usr/bin"
    "/usr/sbin"
    "/bin"
    "/sbin"
    "/Library/Apple/usr/bin"
    "/System/Cryptexes/App/usr/bin"

    # Apple cryptex paths
    "${codex_bootstrap}/local/bin"
    "${codex_bootstrap}/bin"
    "${codex_bootstrap}/appleinternal/bin"
)

# Collect valid paths
declare -a filtered_paths=()
for dir in "${path_candidates[@]}"; do
    [[ -d "$dir" ]] && filtered_paths+=("$dir")
done

# Set and export PATH
PATH="$(IFS=:; echo "${filtered_paths[*]}")"
export PATH

if [ -f ~/.bashrc ]; then
    # shellcheck disable=SC1091
    source "$HOME/.bashrc"
fi
