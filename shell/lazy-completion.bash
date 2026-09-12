# SPDX-License-Identifier: AGPL-3.0-or-later

# Completion values are data. Quote every insertion ourselves, including the
# first Tab: Readline's filename quoting depends on the user's shell settings.
_lazyros_completion_token()
{
    local line=$1 point=$2 character next quote='' escaped=0 index start=0
    _LAZYROS_TOKEN_VALUE=
    for ((index = 0; index < ${#line}; index++)); do
        character=${line:index:1}
        if ((escaped)); then
            if ((index < point)); then
                _LAZYROS_TOKEN_VALUE+=$character
            fi
            escaped=0
            continue
        fi
        if [[ $character == \\ && $quote != "'" ]]; then
            next=${line:index+1:1}
            if [[ $quote == '"' && $next != [\\\$\`\"] ]]; then
                if ((index < point)); then
                    _LAZYROS_TOKEN_VALUE+=$character
                fi
                continue
            fi
            escaped=1
            continue
        fi
        if [[ -n $quote ]]; then
            if [[ $character == "$quote" ]]; then
                quote=
            elif ((index < point)); then
                _LAZYROS_TOKEN_VALUE+=$character
            fi
            continue
        fi
        case $character in
            "'" | '"') quote=$character ;;
            [[:space:]] | ';' | '|' | '&' | '(' | ')' | '<' | '>')
                if ((index >= point)); then
                    break
                fi
                start=$((index + 1))
                _LAZYROS_TOKEN_VALUE=
                ;;
            *)
                if ((index < point)); then
                    _LAZYROS_TOKEN_VALUE+=$character
                fi
                ;;
        esac
    done
    _LAZYROS_TOKEN_START=$start
    _LAZYROS_TOKEN_END=$index
    _LAZYROS_TOKEN_BEFORE=${line:0:start}
    _LAZYROS_TOKEN_AFTER=${line:index}
    _LAZYROS_TOKEN_FIRST=${line:start:1}
}

_lazyros_quote_completion()
{
    local value=$1 character index
    _LAZYROS_QUOTED_COMPLETION=
    for ((index = 0; index < ${#value}; index++)); do
        character=${value:index:1}
        case $character in
            [[:space:]] | \\ | "'" | '"' | '$' | '`' | '*' | '?' | '[' | ']' | '(' | ')' | '{' | '}' | ';' | '&' | '|' | '<' | '>' | '!' | '~' | '#')
                _LAZYROS_QUOTED_COMPLETION+=\\$character
                ;;
            *) _LAZYROS_QUOTED_COMPLETION+=$character ;;
        esac
    done
}

_lazyros_quote_readline_completion()
{
    local value=$1 quote=$2 character index
    if [[ $quote != "'" && $quote != '"' ]]; then
        _lazyros_quote_completion "$value"
        return
    fi
    _LAZYROS_QUOTED_COMPLETION=$quote
    for ((index = 0; index < ${#value}; index++)); do
        character=${value:index:1}
        if [[ $quote == "'" ]]; then
            if [[ $character == "'" ]]; then
                _LAZYROS_QUOTED_COMPLETION+="'\\''"
            else
                _LAZYROS_QUOTED_COMPLETION+=$character
            fi
            continue
        fi
        case $character in
            \\ | '$' | '`' | '"') _LAZYROS_QUOTED_COMPLETION+=\\$character ;;
            '!') _LAZYROS_QUOTED_COMPLETION+=$'"\x27!\x27"' ;;
            *) _LAZYROS_QUOTED_COMPLETION+=$character ;;
        esac
    done
    _LAZYROS_QUOTED_COMPLETION+=$quote
}

_lazyros_insert_completion()
{
    _lazyros_quote_completion "$1"
    READLINE_LINE=${_LAZYROS_TOKEN_BEFORE}${_LAZYROS_QUOTED_COMPLETION}${_LAZYROS_TOKEN_AFTER}
    READLINE_POINT=$((${#_LAZYROS_TOKEN_BEFORE} + ${#_LAZYROS_QUOTED_COMPLETION}))
}

_lazyros_completion_byte_prefix()
{
    # COMP_POINT counts bytes; bind -x READLINE_POINT counts characters on
    # supported Bash versions. Normalize once before scanning completion text.
    local LC_ALL=C
    _LAZYROS_BYTE_PREFIX=${1:0:$2}
}

_lazyros_clear_completion()
{
    if [[ ${_LAZYROS_COMPLETION_ARMED:-0} == 1 ]]; then
        bind '"\C-i": complete'
    fi
    _LAZYROS_COMPLETION_ARMED=0
    unset _LAZYROS_COMPLETION_BEFORE _LAZYROS_COMPLETION_AFTER
    unset _LAZYROS_COMPLETION_PREFIX _LAZYROS_COMPLETION_CANDIDATES
}

_lazyros_completion_reset()
{
    local previous_status=$?
    _lazyros_clear_completion
    return "$previous_status"
}

_lazyros_install_completion_reset()
{
    local prompt_command
    for prompt_command in "${PROMPT_COMMAND[@]}"; do
        [[ $prompt_command == _lazyros_completion_reset ]] && return 0
    done
    # Preserve both string and array prompt hooks in the user's ordinary shell.
    PROMPT_COMMAND+=("_lazyros_completion_reset")
}

_lazyros_second_tab()
{
    local candidate common_prefix selected
    local -a filtered=()
    bind '"\C-i": complete'
    _LAZYROS_COMPLETION_ARMED=0
    _lazyros_completion_token "$READLINE_LINE" "$READLINE_POINT"

    if [[ $_LAZYROS_TOKEN_BEFORE != "${_LAZYROS_COMPLETION_BEFORE:-}" ||
        $_LAZYROS_TOKEN_AFTER != "${_LAZYROS_COMPLETION_AFTER:-}" ]]; then
        _lazyros_clear_completion
        return 0
    fi

    if [[ $_LAZYROS_TOKEN_VALUE == "${_LAZYROS_COMPLETION_PREFIX:-}" ]]; then
        selected=$(command lazy __select -- "${_LAZYROS_COMPLETION_CANDIDATES[@]}") || selected=
        if [[ -n $selected ]]; then
            _lazyros_insert_completion "$selected"
        fi
        _lazyros_clear_completion
        return 0
    fi

    # An edit between Tabs starts a new completion. Reuse the previous domain
    # only while the surrounding command and arguments remain unchanged.
    for candidate in "${_LAZYROS_COMPLETION_CANDIDATES[@]}"; do
        [[ $candidate == "$_LAZYROS_TOKEN_VALUE"* ]] && filtered+=("$candidate")
    done
    if ((${#filtered[@]} == 0)); then
        _lazyros_clear_completion
        return 0
    fi
    common_prefix=${filtered[0]}
    for candidate in "${filtered[@]:1}"; do
        while [[ $candidate != "$common_prefix"* ]]; do
            common_prefix=${common_prefix%?}
        done
    done
    _lazyros_insert_completion "$common_prefix"
    if ((${#filtered[@]} == 1)); then
        _lazyros_clear_completion
        return 0
    fi
    _LAZYROS_COMPLETION_PREFIX=$common_prefix
    _LAZYROS_COMPLETION_CANDIDATES=("${filtered[@]}")
    bind -x '"\C-i":_lazyros_second_tab'
    _LAZYROS_COMPLETION_ARMED=1
}

_lazyros_completion_candidates()
{
    local cursor=$1 candidate common_prefix prefix suffix token_end
    shift
    local -a candidates=() insertions=() completion_words=("$@")
    COMPREPLY=()
    _lazyros_completion_byte_prefix "$COMP_LINE" "$COMP_POINT"
    _lazyros_completion_token "$COMP_LINE" "${#_LAZYROS_BYTE_PREFIX}"
    prefix=$_LAZYROS_TOKEN_VALUE
    token_end=$_LAZYROS_TOKEN_END
    _lazyros_completion_token "$COMP_LINE" "$token_end"
    suffix=${_LAZYROS_TOKEN_VALUE:${#prefix}}
    completion_words[cursor]=$prefix
    while IFS= read -r candidate; do
        [[ $candidate == "$prefix"* ]] || continue
        # Readline retains text after the cursor. Offer only candidates that
        # retain that suffix, and avoid inserting the suffix a second time.
        [[ -z $suffix || $candidate == *"$suffix" ]] || continue
        candidates+=("$candidate")
        insertions+=("${candidate%"$suffix"}")
        _lazyros_quote_readline_completion "${candidate%"$suffix"}" "$_LAZYROS_TOKEN_FIRST"
        COMPREPLY+=("$_LAZYROS_QUOTED_COMPLETION")
    done < <(
        command lazy __complete \
            --shell bash \
            --cursor "$cursor" \
            -- "${completion_words[@]}" 2>/dev/null
    )

    if ((${#COMPREPLY[@]} == 0)); then
        compopt +o noquote
        _lazyros_clear_completion
        return 0
    fi
    compopt -o noquote
    if [[ -n $suffix ]]; then
        compopt -o nospace
    fi
    if ((${#COMPREPLY[@]} <= 1)); then
        _lazyros_clear_completion
        return 0
    fi
    common_prefix=${insertions[0]}
    for candidate in "${insertions[@]:1}"; do
        while [[ $candidate != "$common_prefix"* ]]; do
            common_prefix=${common_prefix%?}
        done
    done
    _LAZYROS_COMPLETION_BEFORE=$_LAZYROS_TOKEN_BEFORE
    _LAZYROS_COMPLETION_AFTER=$_LAZYROS_TOKEN_AFTER
    _LAZYROS_COMPLETION_PREFIX=$common_prefix
    _LAZYROS_COMPLETION_CANDIDATES=("${candidates[@]}")
    bind -x '"\C-i":_lazyros_second_tab'
    _LAZYROS_COMPLETION_ARMED=1
}

_lazyros_complete_lazy()
{
    _lazyros_completion_candidates "$COMP_CWORD" "${COMP_WORDS[@]}"
}

_lazyros_complete_direct()
{
    _lazyros_completion_candidates "$((COMP_CWORD + 1))" lazy "${COMP_WORDS[@]}"
}
