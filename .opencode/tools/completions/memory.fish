# Fish completion for memory.sh
# Add to ~/.config/fish/completions/memory.fish

set -l commands add search context list delete stats session
set -l session_subcommands add list show update delete clear archive use current sessions list-all show-all
set -l categories architecture discovery pattern gotcha config entity decision todo reference context

complete -c memory.sh -f

# Top-level commands
complete -c memory.sh -n "not __fish_seen_subcommand_from $commands" -a add -d 'Add a new memory'
complete -c memory.sh -n "not __fish_seen_subcommand_from $commands" -a search -d 'Search memories'
complete -c memory.sh -n "not __fish_seen_subcommand_from $commands" -a context -d 'Get context block for a topic'
complete -c memory.sh -n "not __fish_seen_subcommand_from $commands" -a list -d 'List all memories'
complete -c memory.sh -n "not __fish_seen_subcommand_from $commands" -a delete -d 'Delete a memory'
complete -c memory.sh -n "not __fish_seen_subcommand_from $commands" -a stats -d 'Show statistics'
complete -c memory.sh -n "not __fish_seen_subcommand_from $commands" -a session -d 'Session memory commands'

# Session subcommands
complete -c memory.sh -n "__fish_seen_subcommand_from session; and not __fish_seen_subcommand_from $session_subcommands" -a add -d 'Add session entry'
complete -c memory.sh -n "__fish_seen_subcommand_from session; and not __fish_seen_subcommand_from $session_subcommands" -a list -d 'List session entries'
complete -c memory.sh -n "__fish_seen_subcommand_from session; and not __fish_seen_subcommand_from $session_subcommands" -a show -d 'Show session state'
complete -c memory.sh -n "__fish_seen_subcommand_from session; and not __fish_seen_subcommand_from $session_subcommands" -a update -d 'Update entry status'
complete -c memory.sh -n "__fish_seen_subcommand_from session; and not __fish_seen_subcommand_from $session_subcommands" -a delete -d 'Delete session entry'
complete -c memory.sh -n "__fish_seen_subcommand_from session; and not __fish_seen_subcommand_from $session_subcommands" -a clear -d 'Clear current session'
complete -c memory.sh -n "__fish_seen_subcommand_from session; and not __fish_seen_subcommand_from $session_subcommands" -a archive -d 'Move to knowledge'
complete -c memory.sh -n "__fish_seen_subcommand_from session; and not __fish_seen_subcommand_from $session_subcommands" -a use -d 'Switch to session'
complete -c memory.sh -n "__fish_seen_subcommand_from session; and not __fish_seen_subcommand_from $session_subcommands" -a current -d 'Show current session info'
complete -c memory.sh -n "__fish_seen_subcommand_from session; and not __fish_seen_subcommand_from $session_subcommands" -a sessions -d 'List all sessions'
complete -c memory.sh -n "__fish_seen_subcommand_from session; and not __fish_seen_subcommand_from $session_subcommands" -a list-all -d 'List entries from all sessions'
complete -c memory.sh -n "__fish_seen_subcommand_from session; and not __fish_seen_subcommand_from $session_subcommands" -a show-all -d 'Show state of all sessions'

# Categories for add
complete -c memory.sh -n "__fish_seen_subcommand_from add; and not __fish_seen_subcommand_from $categories" -a "$categories" -d 'Category'

# Global options
complete -c memory.sh -l help -d 'Show help'
complete -c memory.sh -l version -d 'Show version'
complete -c memory.sh -l quiet -d 'Suppress non-essential output'
complete -c memory.sh -s t -l tags -d 'Comma-separated tags' -r
complete -c memory.sh -s l -l limit -d 'Limit results' -a '5 10 20 50 100'
complete -c memory.sh -s c -l category -d 'Filter by category' -a "$categories"
complete -c memory.sh -s s -l status -d 'Filter/set status' -a 'pending in_progress completed blocked'
complete -c memory.sh -s S -l session -d 'Session name' -r
complete -c memory.sh -s o -l output -d 'Output format' -a 'text json'
