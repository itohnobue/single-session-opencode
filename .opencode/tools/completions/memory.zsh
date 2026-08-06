#compdef memory.sh

# Zsh completion for memory.sh
# Add to your .zshrc or place in a directory in your $fpath:
# fpath=(~/.zsh/completions $fpath)

_memory() {
    local -a commands categories session_subcommands

    commands=(
        'add:Add a new memory'
        'search:Search memories (ranked by relevance + recency)'
        'context:Get context block for a topic'
        'list:List all memories'
        'delete:Delete a memory'
        'stats:Show statistics'
        'session:Session memory commands'
    )

    session_subcommands=(
        'add:Add session entry'
        'list:List session entries'
        'show:Show session state'
        'update:Update entry status'
        'delete:Delete session entry'
        'clear:Clear current session'
        'archive:Move to knowledge'
        'use:Switch to session'
        'current:Show current session info'
        'sessions:List all sessions'
        'list-all:List entries from all sessions'
        'show-all:Show state of all sessions'
    )

    categories=(
        'architecture:System design, structure'
        'discovery:Things learned during exploration'
        'pattern:Code patterns, conventions'
        'gotcha:Bugs, workarounds, edge cases'
        'config:Configuration, environment'
        'entity:Key classes, functions, APIs'
        'decision:Design decisions, rationale'
        'todo:Pending items, follow-ups'
        'reference:External links, docs'
        'context:Project-specific context'
    )

    _arguments -C \
        '1:command:->command' \
        '*::arg:->args' \
        '(-h --help)'{-h,--help}'[Show help message]' \
        '(-v --version)'{-v,--version}'[Show version]' \
        '(-q --quiet)'{-q,--quiet}'[Suppress non-essential output]' \
        '(-t --tags)'{-t,--tags}'[Comma-separated tags]:tags:' \
        '(-l --limit)'{-l,--limit}'[Limit results]:limit:(5 10 20 50 100)' \
        '(-c --category)'{-c,--category}'[Filter by category]:category:(${categories%%:*})' \
        '(-s --status)'{-s,--status}'[Filter/set status]:status:(pending in_progress completed blocked)' \
        '(-S --session)'{-S,--session}'[Session name]:session:' \
        '(-o --output)'{-o,--output}'[Output format]:format:(text json)'

    case $state in
        command)
            _describe -t commands 'command' commands
            ;;
        args)
            case $words[1] in
                add)
                    if (( CURRENT == 2 )); then
                        _describe -t categories 'category' categories
                    else
                        _message 'content'
                    fi
                    ;;
                search)
                    _message 'search query'
                    ;;
                context)
                    _message 'topic'
                    ;;
                delete)
                    _message 'memory ID'
                    ;;
                session)
                    if (( CURRENT == 2 )); then
                        _describe -t session_subcommands 'session subcommand' session_subcommands
                    else
                        case $words[2] in
                            add)   _message 'category content' ;;
                            list|show|update|delete|clear|archive|use|current|sessions|list-all|show-all) _message 'options' ;;
                        esac
                    fi
                    ;;
            esac
            ;;
    esac
}

_memory "$@"
