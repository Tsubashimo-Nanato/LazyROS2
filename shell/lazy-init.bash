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

_lazyros_complete_lazy()
{
    local candidate
    local common_prefix
    local current_word=${COMP_WORDS[COMP_CWORD]-}
    local expected_line
    local expected_point
    COMPREPLY=()

    while IFS= read -r candidate; do
        if [[ $candidate == "$current_word"* ]]; then
            COMPREPLY+=("$candidate")
        fi
    done < <(
        command lazy __complete \
            --shell bash \
            --cursor "$COMP_CWORD" \
            -- "${COMP_WORDS[@]}" 2>/dev/null
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

if type complete >/dev/null 2>&1; then
    complete -o bashdefault -o default -F _lazyros_complete_lazy lazy
fi
