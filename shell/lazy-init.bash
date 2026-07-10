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

_lazyros_complete_lazy()
{
    local candidate
    local current_word=${COMP_WORDS[COMP_CWORD]-}
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
}

if type complete >/dev/null 2>&1; then
    complete -o bashdefault -o default -F _lazyros_complete_lazy lazy
fi
