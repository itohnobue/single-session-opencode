# Bash completion for memory.sh
# Source this file or add to ~/.bashrc:
# source /path/to/memory.bash

_memory_completions() {
    local cur prev commands categories options session_subcommands
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    commands="add search context list delete stats session"
    session_subcommands="add list show update delete clear archive use current sessions list-all show-all"
    categories="architecture discovery pattern gotcha config entity decision todo reference context"
    options="-h --help -t --tags -l --limit -c --category -s --status -S --session -o --output -q --quiet -v --version"

    # First argument: command
    if [[ $COMP_CWORD -eq 1 ]]; then
        COMPREPLY=( $(compgen -W "$commands" -- "$cur") )
        return
    fi

    local cmd="${COMP_WORDS[1]}"

    # Session subcommand as second argument
    if [[ "$cmd" == "session" && $COMP_CWORD -eq 2 ]]; then
        COMPREPLY=( $(compgen -W "$session_subcommands" -- "$cur") )
        return
    fi

    # Options after command
    case "$prev" in
        -t|--tags)     return ;;
        -l|--limit)    COMPREPLY=( $(compgen -W "5 10 20 50 100" -- "$cur") ); return ;;
        -c|--category) COMPREPLY=( $(compgen -W "$categories" -- "$cur") ); return ;;
        -s|--status)   COMPREPLY=( $(compgen -W "pending in_progress completed blocked" -- "$cur") ); return ;;
        -S|--session)  return ;;
        -o|--output)   COMPREPLY=( $(compgen -W "text json" -- "$cur") ); return ;;
    esac

    # Add: category as second arg
    if [[ "$cmd" == "add" && $COMP_CWORD -eq 2 ]]; then
        COMPREPLY=( $(compgen -W "$categories" -- "$cur") )
        return
    fi

    COMPREPLY=( $(compgen -W "$options" -- "$cur") )
}

complete -F _memory_completions memory.sh
complete -F _memory_completions memory.bat
