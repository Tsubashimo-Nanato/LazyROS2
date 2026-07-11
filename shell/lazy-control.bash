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

build()
{
    command lazy build "$@"
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
    command lazy run "$@"
}

launch()
{
    command lazy launch "$@"
}

rviz()
{
    command lazy rviz "$@"
}

rviz2()
{
    command lazy rviz2 "$@"
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

version()
{
    command lazy version "$@"
}
node()
{
    command lazy node "$@"
}
topic()
{
    command lazy topic "$@"
}
service()
{
    command lazy service "$@"
}
action()
{
    command lazy action "$@"
}
param()
{
    command lazy param "$@"
}
bag()
{
    command lazy bag "$@"
}
list()
{
    command lazy list "$@"
}
pkg()
{
    command lazy pkg "$@"
}
interface()
{
    command lazy interface "$@"
}
doctor()
{
    command lazy doctor "$@"
}
wtf()
{
    command lazy wtf "$@"
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
        test) test "$@" ;;
        test-result) test-result "$@" ;;
        run) run "$@" ;;
        launch) launch "$@" ;;
        rviz) rviz "$@" ;;
        rviz2) rviz2 "$@" ;;
        jobs) jobs "$@" ;;
        status) status "$@" ;;
        refresh) refresh "$@" ;;
        config) config "$@" ;;
        help) help "$@" ;;
        about) about "$@" ;;
        version) version "$@" ;;
        node) node "$@" ;;
        topic) topic "$@" ;;
        service) service "$@" ;;
        action) action "$@" ;;
        param) param "$@" ;;
        bag) bag "$@" ;;
        list) list "$@" ;;
        pkg) pkg "$@" ;;
        interface) interface "$@" ;;
        doctor) doctor "$@" ;;
        wtf) wtf "$@" ;;
        exit) builtin exit ;;
        *) command lazy "$command_name" "$@" ;;
    esac
}

_lazyros_second_tab()
{
    local candidate
    local character
    local common_prefix
    local context=${READLINE_LINE-}'|'${READLINE_POINT-0}
    local current_word
    local end
    local expected_line
    local expected_point
    local -a filtered=()
    local selected
    local start=${_LAZYROS_COMPLETION_TOKEN_START:-0}

    bind '"\C-i": complete'
    if [[ ${_LAZYROS_COMPLETION_EXPECTED:-} != "$context" ]]; then
        if ((READLINE_POINT < start)) ||
            [[ ${READLINE_LINE:0:start} != "${_LAZYROS_COMPLETION_EXPECTED:0:start}" ]]; then
            unset _LAZYROS_COMPLETION_EXPECTED _LAZYROS_COMPLETION_TOKEN_START
            unset _LAZYROS_COMPLETION_PREFIX _LAZYROS_COMPLETION_CANDIDATES
            return 0
        fi
        end=$READLINE_POINT
        while ((end < ${#READLINE_LINE})); do
            character=${READLINE_LINE:end:1}
            [[ $character == [[:space:]] ]] && break
            ((end += 1))
        done
        current_word=${READLINE_LINE:start:READLINE_POINT-start}
        for candidate in "${_LAZYROS_COMPLETION_CANDIDATES[@]}"; do
            [[ $candidate == "$current_word"* ]] && filtered+=("$candidate")
        done
        if ((${#filtered[@]} == 0)); then
            unset _LAZYROS_COMPLETION_EXPECTED _LAZYROS_COMPLETION_TOKEN_START
            unset _LAZYROS_COMPLETION_PREFIX _LAZYROS_COMPLETION_CANDIDATES
            return 0
        fi
        common_prefix=${filtered[0]}
        for candidate in "${filtered[@]:1}"; do
            while [[ $candidate != "$common_prefix"* ]]; do
                common_prefix=${common_prefix%?}
            done
        done
        READLINE_LINE=${READLINE_LINE:0:start}${common_prefix}${READLINE_LINE:end}
        READLINE_POINT=$((start + ${#common_prefix}))
        if ((${#filtered[@]} > 1)); then
            expected_line=$READLINE_LINE
            expected_point=$READLINE_POINT
            _LAZYROS_COMPLETION_EXPECTED=$expected_line'|'$expected_point
            _LAZYROS_COMPLETION_PREFIX=$common_prefix
            _LAZYROS_COMPLETION_CANDIDATES=("${filtered[@]}")
            bind -x '"\C-i":_lazyros_second_tab'
            return 0
        fi
        unset _LAZYROS_COMPLETION_EXPECTED _LAZYROS_COMPLETION_TOKEN_START
        unset _LAZYROS_COMPLETION_PREFIX _LAZYROS_COMPLETION_CANDIDATES
        return 0
    fi

    selected=$(command lazy __select -- "${_LAZYROS_COMPLETION_CANDIDATES[@]}") || selected=
    if [[ -n $selected ]]; then
        READLINE_LINE=${READLINE_LINE:0:start}${selected}${READLINE_LINE:READLINE_POINT}
        READLINE_POINT=$((start + ${#selected}))
    fi
    unset _LAZYROS_COMPLETION_EXPECTED _LAZYROS_COMPLETION_TOKEN_START
    unset _LAZYROS_COMPLETION_PREFIX _LAZYROS_COMPLETION_CANDIDATES
}

_lazyros_completion_candidates()
{
    local cursor=$1
    local current_word=$2
    local candidate
    local common_prefix
    local expected_line
    local expected_point
    shift 2
    COMPREPLY=()

    while IFS= read -r candidate; do
        if [[ $candidate == "$current_word"* ]]; then
            COMPREPLY+=("$candidate")
        fi
    done < <(
        command lazy __complete \
            --shell bash \
            --cursor "$cursor" \
            -- "$@" 2>/dev/null
    )

    if ((${#COMPREPLY[@]} <= 1)); then
        return 0
    fi

    common_prefix=${COMPREPLY[0]}
    for candidate in "${COMPREPLY[@]:1}"; do
        while [[ $candidate != "$common_prefix"* ]]; do
            common_prefix=${common_prefix%?}
        done
    done
    expected_line=${COMP_LINE:0:COMP_POINT-${#current_word}}${common_prefix}${COMP_LINE:COMP_POINT}
    expected_point=$((COMP_POINT - ${#current_word} + ${#common_prefix}))
    _LAZYROS_COMPLETION_EXPECTED=$expected_line'|'$expected_point
    _LAZYROS_COMPLETION_TOKEN_START=$((COMP_POINT - ${#current_word}))
    _LAZYROS_COMPLETION_PREFIX=$common_prefix
    _LAZYROS_COMPLETION_CANDIDATES=("${COMPREPLY[@]}")
    bind -x '"\C-i":_lazyros_second_tab'
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
        build test run launch rviz rviz2 jobs status refresh config help about \
        version node topic service action param bag list pkg interface doctor wtf
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
_lazyros_ros_label=${ROS_DISTRO:-none}
_lazyros_ros_label=${_lazyros_ros_label//[^[:alnum:]_.-]/?}

if [[ -t 1 && ${TERM:-dumb} != dumb && -z ${NO_COLOR+x} ]]; then
    PS1="\[\033[38;5;110m\][lazy:${_lazyros_workspace_label} | ros:${_lazyros_ros_label}]\[\033[0m\] \u@\h:\w\\$ "
else
    PS1="[lazy:${_lazyros_workspace_label} | ros:${_lazyros_ros_label}] \u@\h:\w\\$ "
fi

unset _lazyros_workspace_label _lazyros_ros_label

if ! builtin cd -- "$LAZYROS_WORKSPACE"; then
    printf 'lazy: cannot enter workspace: %s\n' "$LAZYROS_WORKSPACE" >&2
    return 2
fi

if ! _lazyros_reload_overlay; then
    printf 'lazy: failed to load workspace overlay: %s/install\n' "$LAZYROS_WORKSPACE" >&2
fi
