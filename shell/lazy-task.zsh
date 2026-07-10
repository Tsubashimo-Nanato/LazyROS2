# SPDX-License-Identifier: AGPL-3.0-or-later

# Source this file from the isolated ZDOTDIR/.zshrc created for one task.

if [[ -z ${LAZYROS_WORKSPACE:-} || $LAZYROS_WORKSPACE != /* ]]; then
    print -u2 -r -- 'lazy: LAZYROS_WORKSPACE must be an absolute path.'
    return 2
fi

if [[ -z ${LAZYROS_JOB_ID:-} || $LAZYROS_JOB_ID == *$'\n'* || $LAZYROS_JOB_ID == *$'\r'* ]]; then
    print -u2 -r -- 'lazy: LAZYROS_JOB_ID is missing or invalid.'
    return 2
fi

_lazyros_task_overlay()
{
    local setup_file=

    setup_file=$(command lazy __setup-path --shell zsh) || return $?
    [[ -n $setup_file ]] || return 0

    source "$setup_file"
}

_lazyros_command_at()
{
    local command_name=$1
    local argument
    shift

    for argument in "$@"; do
        if [[ $argument == -- ]]; then
            break
        fi

        if [[ $argument == --window ]]; then
            print -u2 -r -- 'lazy: task windows cannot open nested task windows.'
            return 2
        fi

        if [[ $argument == --here ]]; then
            command lazy "$command_name" "$@"
            return $?
        fi
    done

    command lazy "$command_name" --here "$@"
}

run() { _lazyros_command_at run "$@"; }
launch() { _lazyros_command_at launch "$@"; }
rviz() { _lazyros_command_at rviz "$@"; }

lazy()
{
    local command_name=${1-}

    if [[ -z $command_name ]]; then
        command lazy help
        return $?
    fi

    shift
    case $command_name in
        run) run "$@" ;;
        launch) launch "$@" ;;
        rviz) rviz "$@" ;;
        __job-run)
            if (( $# != 1 )) || [[ $1 != "$LAZYROS_JOB_ID" ]]; then
                print -u2 -r -- 'lazy: this task can only restart its own job.'
                return 2
            fi
            _lazyros_run_job
            ;;
        *) command lazy "$command_name" "$@" ;;
    esac
}

_lazyros_terminal_supports_osc()
{
    if [[ ! -t 1 || ! -w /dev/tty ]]; then
        return 1
    fi

    if [[ -n ${NO_COLOR+x} || -n ${TMUX:-} || ${TERM:-dumb} == dumb ]]; then
        return 1
    fi

    case ${TERM:-} in
        xterm*|vte*|gnome*|konsole*|kitty*|alacritty*|ghostty*) return 0 ;;
        *) return 1 ;;
    esac
}

_lazyros_valid_color()
{
    [[ ${1-} =~ '^#[0-9A-Fa-f]{6}$' ]]
}

_lazyros_safe_title()
{
    local title=${LAZYROS_WINDOW_TITLE:-}
    [[ -n $title ]] || return 1
    [[ $title != *$'\033'* && $title != *$'\a'* ]]
    [[ $title != *$'\n'* && $title != *$'\r'* ]]
}

_lazyros_restore_terminal()
{
    if (( ${_LAZYROS_OSC_ACTIVE:-0} == 0 )); then
        return
    fi

    printf '\033]111\a\033]110\a\033]112\a\033]0;\a' > /dev/tty
    _LAZYROS_OSC_ACTIVE=0
}

_lazyros_apply_terminal()
{
    _LAZYROS_OSC_ACTIVE=0
    _lazyros_terminal_supports_osc || return 0
    _lazyros_valid_color "${LAZYROS_COLOR_BACKGROUND:-}" || return 0
    _lazyros_valid_color "${LAZYROS_COLOR_FOREGROUND:-}" || return 0
    _lazyros_valid_color "${LAZYROS_COLOR_ACCENT:-}" || return 0

    printf '\033]11;%s\a\033]10;%s\a\033]12;%s\a' \
        "$LAZYROS_COLOR_BACKGROUND" \
        "$LAZYROS_COLOR_FOREGROUND" \
        "$LAZYROS_COLOR_ACCENT" > /dev/tty

    if _lazyros_safe_title; then
        printf '\033]0;%s\a' "$LAZYROS_WINDOW_TITLE" > /dev/tty
    fi

    _LAZYROS_OSC_ACTIVE=1
}

_lazyros_run_job()
{
    if ! _lazyros_task_overlay; then
        print -u2 -r -- "lazy: failed to load workspace overlay: $LAZYROS_WORKSPACE/install"
        if ! command lazy __job-finish "$LAZYROS_JOB_ID" "$$" 1; then
            print -u2 -r -- 'lazy: failed to update the task registry after overlay failure.'
        fi
        return 1
    fi

    command lazy __job-run "$LAZYROS_JOB_ID"
}

TRAPEXIT()
{
    _lazyros_restore_terminal
}

TRAPHUP()
{
    _lazyros_restore_terminal
    exit 129
}

TRAPTERM()
{
    _lazyros_restore_terminal
    exit 143
}

fc -p /dev/null
HISTSIZE=100
SAVEHIST=0
if [[ -n ${LAZYROS_RESTART_LINE:-} ]]; then
    print -sr -- "$LAZYROS_RESTART_LINE"
fi

if [[ ${LAZYROS_JOB_NUMBER:-} =~ '^[0-9]{1,9}$' ]]; then
    printf -v _lazyros_job_label '%02d' "$((10#$LAZYROS_JOB_NUMBER))"
else
    _lazyros_job_label=${LAZYROS_JOB_ID//[^[:alnum:]_.-]/?}
fi
_lazyros_ros_label=${ROS_DISTRO:-none}
_lazyros_ros_label=${_lazyros_ros_label//[^[:alnum:]_.-]/?}
if [[ -t 1 && ${TERM:-dumb} != dumb && -z ${NO_COLOR+x} ]]; then
    PROMPT="%F{110}[lazy job #${_lazyros_job_label} | ros:${_lazyros_ros_label}]%f %~%# "
else
    PROMPT="[lazy job #${_lazyros_job_label} | ros:${_lazyros_ros_label}] %~%# "
fi
unset _lazyros_job_label _lazyros_ros_label

if ! builtin cd -- "$LAZYROS_WORKSPACE"; then
    print -u2 -r -- "lazy: cannot enter workspace: $LAZYROS_WORKSPACE"
    return 2
fi

if ! command lazy __job-claim "$LAZYROS_JOB_ID" "$$"; then
    print -u2 -r -- 'lazy: failed to claim the task-shell registry record.'
    return 3
fi

_lazyros_apply_terminal
_lazyros_run_job
