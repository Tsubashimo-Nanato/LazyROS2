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
    local setup_file= generation=
    if [[ -r ${LAZYROS_OVERLAY_GENERATION_FILE:-} ]]; then
        IFS= read -r generation < "$LAZYROS_OVERLAY_GENERATION_FILE"
    fi

    setup_file=$(command lazy __setup-path --shell bash) || return $?
    if [[ -n $setup_file ]]; then
        # Python proved this path remains inside the workspace install tree.
        # shellcheck disable=SC1090
        source "$setup_file" || return $?
    fi
    _LAZYROS_OVERLAY_GENERATION=$generation
}

_lazyros_check_overlay()
{
    local previous_status=$? generation=
    if [[ -r ${LAZYROS_OVERLAY_GENERATION_FILE:-} ]]; then
        IFS= read -r generation < "$LAZYROS_OVERLAY_GENERATION_FILE"
    fi
    if [[ -n $generation && $generation != "${_LAZYROS_OVERLAY_GENERATION:-}" ]]; then
        if ! _lazyros_reload_overlay; then
            printf 'lazy: failed to load workspace overlay: %s/install\n' "$LAZYROS_WORKSPACE" >&2
        fi
    fi
    return "$previous_status"
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

# shellcheck source-path=SCRIPTDIR
# shellcheck source=lazy-completion.bash
source "${BASH_SOURCE[0]%/*}/lazy-completion.bash"

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
PROMPT_COMMAND=(_lazyros_completion_reset _lazyros_check_overlay)
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
