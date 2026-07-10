# SPDX-License-Identifier: AGPL-3.0-or-later

# Source this file from the isolated ZDOTDIR/.zshrc created by the CLI.

if [[ -z ${LAZYROS_WORKSPACE:-} || $LAZYROS_WORKSPACE != /* ]]; then
    print -u2 -r -- 'lazy: LAZYROS_WORKSPACE must be an absolute path.'
    return 2
fi

if [[ -z ${LAZYROS_HISTORY_FILE:-} || $LAZYROS_HISTORY_FILE != /* ]]; then
    print -u2 -r -- 'lazy: LAZYROS_HISTORY_FILE must be an absolute path.'
    return 2
fi

_lazyros_reload_overlay()
{
    local setup_file=

    setup_file=$(command lazy __setup-path --shell zsh) || return $?
    [[ -n $setup_file ]] || return 0

    source "$setup_file"
}

_lazyros_command_at()
{
    local command_name=$1
    local default_location=$2
    local argument
    shift 2

    for argument in "$@"; do
        if [[ $argument == -- ]]; then
            break
        fi

        if [[ $argument == --here || $argument == --window ]]; then
            command lazy "$command_name" "$@"
            return $?
        fi
    done

    command lazy "$command_name" "$default_location" "$@"
}

build()
{
    local command_status
    command lazy build "$@"
    command_status=$?

    if (( command_status != 0 )); then
        return "$command_status"
    fi

    _lazyros_reload_overlay
}

test() { command lazy test "$@"; }
test-result() { command lazy test-result "$@"; }
run() { _lazyros_command_at run --window "$@"; }
launch() { _lazyros_command_at launch --window "$@"; }
rviz() { _lazyros_command_at rviz --window "$@"; }
jobs() { command lazy jobs "$@"; }
status() { command lazy status "$@"; }
refresh() { command lazy refresh "$@"; }
config() { command lazy config "$@"; }
help() { command lazy help "$@"; }
about() { command lazy about "$@"; }

lazy()
{
    local command_name=${1-}

    if [[ -z $command_name ]]; then
        command lazy help
        return $?
    fi

    shift
    case $command_name in
        build) build "$@" ;;
        test) test "$@" ;;
        test-result) test-result "$@" ;;
        run) run "$@" ;;
        launch) launch "$@" ;;
        rviz) rviz "$@" ;;
        jobs) jobs "$@" ;;
        status) status "$@" ;;
        refresh) refresh "$@" ;;
        config) config "$@" ;;
        help) help "$@" ;;
        about) about "$@" ;;
        exit) builtin exit ;;
        *) command lazy "$command_name" "$@" ;;
    esac
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
        compstate[insert]=''
        compstate[list]=list
    else
        compstate[insert]=unambiguous
        compstate[list]=''
    fi
    compadd -Q -- "$@"
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

_lazyros_complete_direct()
{
    local direct_cursor=$((CURRENT + 1))
    local -a direct_words candidates
    direct_words=(lazy "${words[@]}")
    candidates=("${(@f)$(
        command lazy __complete \
            --shell zsh \
            --cursor "$direct_cursor" \
            -- "${direct_words[@]}" 2>/dev/null
    )}")

    if (( ${#candidates[@]} == 0 )); then
        _lazyros_completion_observe "direct:${CURRENT}"
        return 1
    fi

    _lazyros_compadd "direct:${CURRENT}" "${candidates[@]}"
}

if (( ! $+functions[compdef] )); then
    autoload -Uz compinit
    compinit -C
fi

compdef _lazyros_complete_lazy lazy
compdef _lazyros_complete_direct build test run launch rviz jobs config help

HISTFILE=$LAZYROS_HISTORY_FILE
HISTSIZE=2000
SAVEHIST=2000
setopt append_history hist_ignore_all_dups
fc -p "$HISTFILE"

_lazyros_workspace_label=${LAZYROS_WORKSPACE:t}
_lazyros_workspace_label=${_lazyros_workspace_label//[^[:alnum:]_.-]/?}
_lazyros_ros_label=${ROS_DISTRO:-none}
_lazyros_ros_label=${_lazyros_ros_label//[^[:alnum:]_.-]/?}

if [[ -t 1 && ${TERM:-dumb} != dumb && -z ${NO_COLOR+x} ]]; then
    PROMPT="%F{110}[lazy:${_lazyros_workspace_label} | ros:${_lazyros_ros_label}]%f %n@%m:%~%# "
else
    PROMPT="[lazy:${_lazyros_workspace_label} | ros:${_lazyros_ros_label}] %n@%m:%~%# "
fi

unset _lazyros_workspace_label _lazyros_ros_label

if ! builtin cd -- "$LAZYROS_WORKSPACE"; then
    print -u2 -r -- "lazy: cannot enter workspace: $LAZYROS_WORKSPACE"
    return 2
fi

if ! _lazyros_reload_overlay; then
    print -u2 -r -- "lazy: failed to load workspace overlay: $LAZYROS_WORKSPACE/install"
fi
