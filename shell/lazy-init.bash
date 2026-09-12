# SPDX-License-Identifier: AGPL-3.0-or-later

# This file is sourced from the managed block in ~/.bashrc.

lazy()
{
    if (($# == 0)); then
        command lazy --shell bash
        return $?
    fi

    command lazy "$@"
}

# shellcheck source-path=SCRIPTDIR
# shellcheck source=lazy-completion.bash
source "${BASH_SOURCE[0]%/*}/lazy-completion.bash"

if type complete >/dev/null 2>&1; then
    complete -o bashdefault -o default -F _lazyros_complete_lazy lazy
    _lazyros_install_completion_reset
fi
