# SPDX-License-Identifier: AGPL-3.0-or-later

# Bash reads this file through --rcfile. The CLI validates the workspace and
# captures the clean build environment before starting this shell.

if [[ -z ${LAZYROS_WORKSPACE:-} || $LAZYROS_WORKSPACE != /* ]]; then
    printf '%s\n' 'lazy: LAZYROS_WORKSPACE must be an absolute path.' >&2
    return 2
fi

if [[ -z ${LAZYROS_HISTORY_FILE:-} || $LAZYROS_HISTORY_FILE != /* ]]; then
    printf '%s\n' 'lazy: LAZYROS_HISTORY_FILE must be an absolute path.' >&2
    return 2
fi

_lazyros_reload_overlay()
{
    local setup_file=

    setup_file=$(command lazy __setup-path --shell bash) || return $?
    [[ -n $setup_file ]] || return 0

    # Python returned a realpath proven to remain inside this workspace install tree.
    # shellcheck disable=SC1090
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

    if ((command_status != 0)); then
        return "$command_status"
    fi

    _lazyros_reload_overlay
}

create()
{
    command lazy create "$@"
}

pkg()
{
    command lazy pkg "$@"
}

test()
{
    command lazy test "$@"
}

test-result()
{
    command lazy test-result "$@"
}

run()
{
    _lazyros_command_at run --window "$@"
}

launch()
{
    _lazyros_command_at launch --window "$@"
}

rviz()
{
    _lazyros_command_at rviz --window "$@"
}

jobs()
{
    command lazy jobs "$@"
}

status()
{
    command lazy status "$@"
}

refresh()
{
    command lazy refresh "$@"
}

config()
{
    command lazy config "$@"
}

help()
{
    command lazy help "$@"
}

about()
{
    command lazy about "$@"
}

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
        create) create "$@" ;;
        pkg) pkg "$@" ;;
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

_lazyros_completion_candidates()
{
    local cursor=$1
    local current_word=$2
    local candidate
    local completion_context now elapsed completion_repeat=0
    local -a completion_words help_words
    shift 2
    completion_words=("$@")
    COMPREPLY=()

    completion_context=$cursor:
    if ((cursor > 0)); then
        printf -v completion_context '%s%s\034' "$completion_context" "${completion_words[*]:0:cursor}"
    fi
    now=${EPOCHREALTIME/./}
    [[ $now =~ ^[0-9]+$ ]] || now=$((SECONDS * 1000000))
    if [[ ${_LAZYROS_COMPLETION_CONTEXT:-} == "$completion_context" &&
        ${_LAZYROS_COMPLETION_TIME:-0} =~ ^[0-9]+$ ]]; then
        elapsed=$((10#$now - 10#$_LAZYROS_COMPLETION_TIME))
        ((elapsed >= 0 && elapsed <= 1000000)) && completion_repeat=1
    fi
    _LAZYROS_COMPLETION_CONTEXT=$completion_context
    _LAZYROS_COMPLETION_TIME=$now

    while IFS= read -r candidate; do
        if [[ $candidate == "$current_word"* ]]; then
            COMPREPLY+=("$candidate")
        fi
    done < <(
        command lazy __complete \
            --shell bash \
            --cursor "$cursor" \
            -- "${completion_words[@]}" 2>/dev/null
    )

    if ((completion_repeat)); then
        help_words=("${completion_words[@]}")
        _lazyros_completion_help "${help_words[@]}"
    fi
}

_lazyros_completion_help()
{
    local -a help_path
    [[ ${1-} == lazy ]] && shift
    case ${1-}:${2-} in
        create:package | create:pkg | pkg:create) help_path=("${1-}" "${2-}") ;;
        :*) help_path=() ;;
        *) help_path=("${1-}") ;;
    esac
    command lazy help "${help_path[@]}" </dev/null >/dev/tty 2>&1
}

_lazyros_complete_lazy()
{
    _lazyros_completion_candidates \
        "$COMP_CWORD" \
        "${COMP_WORDS[COMP_CWORD]-}" \
        "${COMP_WORDS[@]}"
}

_lazyros_complete_direct()
{
    local direct_cursor=$((COMP_CWORD + 1))
    _lazyros_completion_candidates \
        "$direct_cursor" \
        "${COMP_WORDS[COMP_CWORD]-}" \
        lazy "${COMP_WORDS[@]}"
}

if type complete >/dev/null 2>&1; then
    complete -o bashdefault -o default -F _lazyros_complete_lazy lazy
    complete -o bashdefault -o default -F _lazyros_complete_direct \
        build create pkg test run launch rviz jobs config help
fi

HISTFILE=$LAZYROS_HISTORY_FILE
HISTSIZE=2000
HISTFILESIZE=4000
HISTCONTROL=ignoredups:erasedups
shopt -s histappend
builtin history -c
if [[ -f $HISTFILE ]]; then
    builtin history -r "$HISTFILE"
fi

_lazyros_workspace_label=${LAZYROS_WORKSPACE##*/}
_lazyros_workspace_label=${_lazyros_workspace_label//[^[:alnum:]_.-]/?}
_lazyros_ros_label=${ROS_DISTRO:-}
_lazyros_ros_label=${_lazyros_ros_label//[^[:alnum:]_.-]/?}
_lazyros_ros_segment=${_lazyros_ros_label:+ | ros:${_lazyros_ros_label}}

_lazyros_prompt_spacing()
{
    local relative parent leaf

    if [[ $PWD == "$LAZYROS_WORKSPACE" ]]; then
        _lazyros_path_label=.
    elif [[ $PWD == "$LAZYROS_WORKSPACE"/* ]]; then
        _lazyros_path_label=${PWD#"$LAZYROS_WORKSPACE"/}
    else
        _lazyros_path_label=${PWD/#"$HOME"/~}
    fi
    relative=$_lazyros_path_label
    if [[ $relative == */*/* ]]; then
        leaf=${relative##*/}
        parent=${relative%/*}
        parent=${parent##*/}
        _lazyros_path_label="…/$parent/$leaf"
    fi
    _lazyros_path_label=${_lazyros_path_label//\\/\\\\}
    _lazyros_path_label=${_lazyros_path_label//\$/\\$}
    _lazyros_path_label=${_lazyros_path_label//\`/\\\`}
    printf '\n'
}
PROMPT_COMMAND=_lazyros_prompt_spacing

if [[ -t 1 && ${TERM:-dumb} != dumb && -z ${NO_COLOR+x} ]]; then
    PS1="\[\033[38;5;110m\][${_lazyros_workspace_label}${_lazyros_ros_segment}]\[\033[0m\] \${_lazyros_path_label}\\$ "
else
    PS1="[${_lazyros_workspace_label}${_lazyros_ros_segment}] \${_lazyros_path_label}\\$ "
fi

unset _lazyros_workspace_label _lazyros_ros_label _lazyros_ros_segment

if ! builtin cd -- "$LAZYROS_WORKSPACE"; then
    printf 'lazy: cannot enter workspace: %s\n' "$LAZYROS_WORKSPACE" >&2
    return 2
fi

if ! _lazyros_reload_overlay; then
    printf 'lazy: failed to load workspace overlay: %s/install\n' "$LAZYROS_WORKSPACE" >&2
fi
