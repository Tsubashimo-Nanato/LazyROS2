# SPDX-License-Identifier: AGPL-3.0-or-later

# This file is sourced from the managed block in ~/.zshrc.

lazy()
{
    if (( $# == 0 )); then
        command lazy --shell zsh
        return $?
    fi

    command lazy "$@"
}

_lazyros_compadd()
{
    local completion_context=$1
    local completion_repeat=0
    shift

    # LASTWIDGET resets after user insertion, deletion, or cursor movement, but
    # stays on the completion widget when the first Tab inserted a prefix.
    if [[ ${_LAZYROS_COMPLETION_CONTEXT:-} == "$completion_context" &&
        ${_LAZYROS_COMPLETION_HISTNO:-} == ${HISTNO:-0} &&
        -n ${_LAZYROS_COMPLETION_WIDGET:-} &&
        ${LASTWIDGET:-} == ${_LAZYROS_COMPLETION_WIDGET} ]]; then
        completion_repeat=1
    fi
    _lazyros_completion_observe "$completion_context"

    if (( completion_repeat )); then
        _lazyros_completion_help "${_LAZYROS_COMPLETION_HELP_WORDS[@]}"
        compstate[insert]=''
        compstate[list]=list
    else
        compstate[insert]=unambiguous
        compstate[list]=''
    fi
    compadd -Q -- "$@"
}

_lazyros_completion_help()
{
    local -a help_path
    [[ ${1-} == lazy ]] && shift
    case ${1-}:${2-} in
        create:package|create:pkg|pkg:create) help_path=(${1-} ${2-}) ;;
        :*) help_path=() ;;
        *) help_path=(${1-}) ;;
    esac
    command lazy help "${help_path[@]}" </dev/null >/dev/tty 2>&1
}

_lazyros_completion_observe()
{
    typeset -g _LAZYROS_COMPLETION_CONTEXT=$1
    typeset -g _LAZYROS_COMPLETION_HISTNO=${HISTNO:-0}
    typeset -g _LAZYROS_COMPLETION_WIDGET=${WIDGET:-}
}

_lazyros_complete_lazy()
{
    local -a candidates
    typeset -ga _LAZYROS_COMPLETION_HELP_WORDS
    _LAZYROS_COMPLETION_HELP_WORDS=("${words[@]}")
    candidates=("${(@f)$(
        command lazy __complete \
            --shell zsh \
            --cursor "$CURRENT" \
            -- "${words[@]}" 2>/dev/null
    )}")

    if (( ${#candidates[@]} == 0 )); then
        _lazyros_completion_observe "lazy:${CURRENT}"
        return 1
    fi

    _lazyros_compadd "lazy:${CURRENT}" "${candidates[@]}"
}

if (( ! $+functions[compdef] )); then
    autoload -Uz compinit
    compinit -C
fi

compdef _lazyros_complete_lazy lazy
