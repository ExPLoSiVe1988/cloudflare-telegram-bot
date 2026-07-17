#!/bin/bash

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
PROJECT_DIR_NAME="cloudflare-telegram-bot"
PROJECT_NAME="Cloudflare Telegram Bot"
PROJECT_DIR="$SCRIPT_DIR"

if [[ "$SCRIPT_DIR" != *"$PROJECT_DIR_NAME"* ]]; then
    PROJECT_DIR="$HOME/$PROJECT_DIR_NAME"
fi

REPO_URL="https://github.com/ExPLoSiVe1988/cloudflare-telegram-bot.git"
ENV_FILE="$PROJECT_DIR/.env"
CONFIG_FILE="$PROJECT_DIR/config.json"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.yml"
COMPOSE_CMD=""

detect_compose_command() {
    if command -v docker &> /dev/null && sudo docker compose version &> /dev/null; then
        COMPOSE_CMD="sudo docker compose -f $COMPOSE_FILE --project-directory $PROJECT_DIR"
        echo -e "${GREEN}Detected Docker Compose V2. Using 'docker compose'.${NC}"
    elif command -v docker-compose &> /dev/null; then
        COMPOSE_CMD="sudo docker-compose -f $COMPOSE_FILE --project-directory $PROJECT_DIR"
        echo -e "${YELLOW}Docker Compose V2 not found. Falling back to legacy 'docker-compose'.${NC}"
    else
        COMPOSE_CMD=""
    fi
}

print_header() {
    clear
    echo -e "${GREEN}"
    cat <<'EOF'
   ██████╗███████╗██████╗  ██████╗ ████████╗
  ██╔════╝██╔════╝██╔══██╗██╔═══██╗╚══██╔══╝
  ██║     █████╗  ██████╔╝██║   ██║   ██║
  ██║     ██╔══╝  ██╔══██╗██║   ██║   ██║
  ╚██████╗██║     ██████╔╝╚██████╔╝   ██║
   ╚═════╝╚═╝     ╚═════╝  ╚═════╝    ╚═╝
EOF
    echo -e "${NC}"
    echo -e "${BLUE}  ┌────────────────────────────────────────────────────────┐${NC}"
    echo -e "${BLUE}  │${NC} ${GREEN}Cloudflare Telegram Bot${NC}                              ${BLUE}│${NC}"
    echo -e "${BLUE}  │${NC} ${YELLOW}Cloudflare + ArvanCloud DNS Manager${NC}                   ${BLUE}│${NC}"
    echo -e "${BLUE}  │${NC} ${GREEN}Powered by @H_ExPLoSiVe / ExPLoSiVe1988${NC}              ${BLUE}│${NC}"
    echo -e "${BLUE}  │${NC} ${YELLOW}Public / Community Package${NC}                            ${BLUE}│${NC}"
    echo -e "${BLUE}  └────────────────────────────────────────────────────────┘${NC}"
    echo -e "${CYAN}  Installing ${PROJECT_NAME} ...${NC}\n"
    sleep 1
}

mask_secret() {
    local value="$1"
    local len=${#value}
    if [ -z "$value" ]; then
        echo ""
    elif [ "$len" -le 10 ]; then
        echo "********"
    else
        echo "${value:0:4}...${value: -4}"
    fi
}

read_env_var() {
    if [ -f "$ENV_FILE" ]; then
        grep -m1 "^${1}=" "$ENV_FILE" | cut -d'=' -f2-
    fi
}

write_env_var() {
    local key="$1"
    local value="$2"
    mkdir -p "$(dirname "$ENV_FILE")"
    touch "$ENV_FILE"

    if grep -q "^${key}=" "$ENV_FILE"; then
        local tmp_file
        tmp_file=$(mktemp)
        grep -v "^${key}=" "$ENV_FILE" > "$tmp_file" || true
        mv "$tmp_file" "$ENV_FILE"
    fi
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
}

ensure_env_var_exists() {
    local key="$1"
    local default_value="$2"
    if [ -f "$ENV_FILE" ] && grep -q "^${key}=" "$ENV_FILE"; then
        return 0
    fi
    write_env_var "$key" "$default_value"
}

ensure_env_defaults() {
    mkdir -p "$PROJECT_DIR"
    touch "$ENV_FILE"
    ensure_env_var_exists "CF_ACCOUNTS" ""
    ensure_env_var_exists "ARVAN_ACCOUNTS" ""
    if ! grep -q "^TIMEZONE=" "$ENV_FILE"; then
        echo "" >> "$ENV_FILE"
        echo "# Timezone for log and report timestamps" >> "$ENV_FILE"
        echo "TIMEZONE=Asia/Tehran" >> "$ENV_FILE"
    fi
}

backup_env_file() {
    if [ -f "$ENV_FILE" ]; then
        local backup_name="$ENV_FILE.bak.$(date +%Y%m%d-%H%M%S)"
        cp "$ENV_FILE" "$backup_name"
        echo -e "${GREEN}.env backup created: $backup_name${NC}"
    fi
}

validate_account_nickname() {
    local nickname="$1"
    if [ -z "$nickname" ]; then
        echo -e "${RED}Nickname cannot be empty.${NC}"
        return 1
    fi
    if [[ "$nickname" == *":"* || "$nickname" == *","* ]]; then
        echo -e "${RED}Nickname cannot contain ':' or ','.${NC}"
        return 1
    fi
    return 0
}

display_accounts_masked() {
    local accounts_string="$1"
    if [ -z "$accounts_string" ]; then
        echo "Current Accounts: None"
        return
    fi

    echo "Current Accounts:"
    IFS=',' read -ra accounts <<< "$accounts_string"
    local i=1
    for acc in "${accounts[@]}"; do
        local nickname token
        nickname=$(echo "$acc" | cut -d':' -f1)
        token=$(echo "$acc" | cut -d':' -f2-)
        echo "  $i) $nickname ($(mask_secret "$token"))"
        i=$((i+1))
    done
}

append_account_entry() {
    local env_key="$1"
    local nickname="$2"
    local token="$3"
    local current_accounts
    current_accounts=$(read_env_var "$env_key")

    local new_entry="$nickname:$token"
    if [ -z "$current_accounts" ]; then
        write_env_var "$env_key" "$new_entry"
    else
        write_env_var "$env_key" "$current_accounts,$new_entry"
    fi
}

remove_account_entry() {
    local env_key="$1"
    local current_accounts
    current_accounts=$(read_env_var "$env_key")

    if [ -z "$current_accounts" ]; then
        echo -e "${YELLOW}No accounts to remove.${NC}"
        return 1
    fi

    IFS=',' read -ra accounts <<< "$current_accounts"
    echo "Select an account to remove:"
    local i=1
    for acc in "${accounts[@]}"; do
        local nickname
        nickname=$(echo "$acc" | cut -d':' -f1)
        echo "  $i) $nickname"
        i=$((i+1))
    done

    read -p "Enter the number of the account to remove (or 0 to cancel): " del_choice
    if [[ "$del_choice" =~ ^[0-9]+$ ]] && [[ "$del_choice" -gt 0 && "$del_choice" -le ${#accounts[@]} ]]; then
        unset "accounts[$((del_choice-1))]"
        local new_accounts_list
        new_accounts_list=$(IFS=,; echo "${accounts[*]}")
        write_env_var "$env_key" "$new_accounts_list"
        echo -e "${GREEN}Account removed.${NC}"
        return 0
    fi

    echo "Removal cancelled."
    return 1
}

prompt_for_restart() {
    detect_compose_command
    if [ -z "$COMPOSE_CMD" ]; then
        echo -e "${YELLOW}Docker Compose not available. Please restart manually later.${NC}"
        return
    fi

    read -p "Apply these changes and restart the bot? (y/n): " confirm_restart
    if [[ "$confirm_restart" == "y" || "$confirm_restart" == "Y" ]]; then
        echo -e "${GREEN}Recreating the container to apply changes...${NC}"
        $COMPOSE_CMD down
        $COMPOSE_CMD up -d --build --remove-orphans
        echo -e "${GREEN}Bot restarted successfully.${NC}"
    else
        echo -e "${YELLOW}Changes saved, but bot was not restarted. Please restart manually to apply.${NC}"
    fi
}

manage_cf_accounts() {
    ensure_env_defaults
    while true; do
        print_header
        echo -e "${YELLOW}--- Manage Cloudflare Accounts ---${NC}"
        local current_cf_accounts
        current_cf_accounts=$(read_env_var "CF_ACCOUNTS")
        display_accounts_masked "$current_cf_accounts"
        echo ""
        echo "1) Add a new Cloudflare account"
        echo "2) Remove an existing Cloudflare account"
        echo "3) Back to previous menu"
        read -p "Choose an option: " cf_choice

        case $cf_choice in
            1)
                read -p "  Enter a nickname for the new Cloudflare account: " cf_nickname
                validate_account_nickname "$cf_nickname" || { read -p "Press Enter..."; continue; }
                read -p "  Enter the Cloudflare API Token: " cf_token
                if [ -z "$cf_token" ]; then
                    echo -e "${RED}Token cannot be empty.${NC}"
                else
                    append_account_entry "CF_ACCOUNTS" "$cf_nickname" "$cf_token"
                    echo -e "${GREEN}Cloudflare account '$cf_nickname' added.${NC}"
                fi
                read -p "Press Enter..."
                ;;
            2)
                remove_account_entry "CF_ACCOUNTS"
                read -p "Press Enter..."
                ;;
            3) break ;;
            *) echo -e "${RED}Invalid option.${NC}"; read -p "Press Enter..." ;;
        esac
    done
}

manage_arvan_accounts() {
    ensure_env_defaults
    while true; do
        print_header
        echo -e "${YELLOW}--- Manage ArvanCloud Accounts ---${NC}"
        local current_arvan_accounts
        current_arvan_accounts=$(read_env_var "ARVAN_ACCOUNTS")
        display_accounts_masked "$current_arvan_accounts"
        echo ""
        echo "1) Add a new ArvanCloud account"
        echo "2) Remove an existing ArvanCloud account"
        echo "3) Back to previous menu"
        read -p "Choose an option: " arvan_choice

        case $arvan_choice in
            1)
                echo -e "${YELLOW}Make sure this Machine User has access to Domain/DNS management in ArvanCloud IAM.${NC}"
                read -p "  Enter a nickname for the new ArvanCloud account: " arvan_nickname
                validate_account_nickname "$arvan_nickname" || { read -p "Press Enter..."; continue; }
                read -p "  Enter the ArvanCloud API Key: " arvan_token
                if [ -z "$arvan_token" ]; then
                    echo -e "${RED}API Key cannot be empty.${NC}"
                else
                    append_account_entry "ARVAN_ACCOUNTS" "$arvan_nickname" "$arvan_token"
                    echo -e "${GREEN}ArvanCloud account '$arvan_nickname' added.${NC}"
                fi
                read -p "Press Enter..."
                ;;
            2)
                remove_account_entry "ARVAN_ACCOUNTS"
                read -p "Press Enter..."
                ;;
            3) break ;;
            *) echo -e "${RED}Invalid option.${NC}"; read -p "Press Enter..." ;;
        esac
    done
}

manage_dns_provider_accounts() {
    ensure_env_defaults
    while true; do
        print_header
        echo -e "${YELLOW}--- Manage DNS Provider Accounts ---${NC}"
        echo "1) Manage Cloudflare Accounts"
        echo "2) Manage ArvanCloud Accounts"
        echo "3) Back to previous menu"
        read -p "Choose an option: " provider_choice

        case $provider_choice in
            1) manage_cf_accounts ;;
            2) manage_arvan_accounts ;;
            3) break ;;
            *) echo -e "${RED}Invalid option.${NC}"; read -p "Press Enter..." ;;
        esac
    done
}

edit_config() {
    if [ ! -d "$PROJECT_DIR" ]; then
        echo -e "${RED}Project directory not found. Please run Install first.${NC}"
        read -p "Press Enter..."
        return
    fi

    ensure_env_defaults
    backup_env_file

    local original_dir
    original_dir=$(pwd)
    cd "$PROJECT_DIR" || { echo -e "${RED}Failed to enter project directory.${NC}"; cd "$original_dir"; return; }

    local config_changed=false
    while true; do
        print_header
        echo -e "${YELLOW}--- Edit Core Configuration (.env) ---${NC}"
        echo "1) Manage Super Admins"
        echo "2) Manage DNS Provider Accounts (Cloudflare / ArvanCloud)"
        echo "3) Edit Telegram Bot Token"
        echo "4) Edit Timezone"
        echo "5) Back to Main Menu"
        read -p "Choose an option: " choice

        case $choice in
            1)
                local current_admins
                current_admins=$(read_env_var "TELEGRAM_ADMIN_IDS")
                echo -e "\nCurrent Super Admins: ${YELLOW}${current_admins:-None}${NC}"
                read -p "Enter the new comma-separated list of Super Admin IDs: " new_admins
                write_env_var "TELEGRAM_ADMIN_IDS" "$new_admins"
                echo -e "${GREEN}Super Admins list updated.${NC}"
                config_changed=true
                read -p "Press Enter..."
                ;;
            2)
                manage_dns_provider_accounts
                config_changed=true
                ;;
            3)
                local current_token
                current_token=$(read_env_var "TELEGRAM_BOT_TOKEN")
                echo "Current Token: $(mask_secret "$current_token")"
                read -p "Enter the new Telegram Bot Token (leave empty to cancel): " new_bot_token
                if [ -n "$new_bot_token" ]; then
                    write_env_var "TELEGRAM_BOT_TOKEN" "$new_bot_token"
                    echo -e "${GREEN}Bot Token updated.${NC}"
                    config_changed=true
                fi
                read -p "Press Enter..."
                ;;
            4)
                local current_timezone
                current_timezone=$(read_env_var "TIMEZONE")
                echo "Current Timezone: ${current_timezone:-Asia/Tehran}"
                read -p "Enter the new timezone [Default: Asia/Tehran]: " new_timezone
                if [ -z "$new_timezone" ]; then new_timezone="Asia/Tehran"; fi
                write_env_var "TIMEZONE" "$new_timezone"
                echo -e "${GREEN}Timezone updated.${NC}"
                config_changed=true
                read -p "Press Enter..."
                ;;
            5) break ;;
            *) echo -e "${RED}Invalid option.${NC}"; read -p "Press Enter..." ;;
        esac
    done

    cd "$original_dir"

    if [ "$config_changed" = true ]; then
        prompt_for_restart
    fi
}

install_bot() {
    print_header
    echo -e "${GREEN}Starting Bot Installation/Reinstallation...${NC}"

    if ! command -v git &> /dev/null || ! command -v curl &> /dev/null; then
        echo -e "${YELLOW}git/curl not found. Installing...${NC}"
        sudo apt-get update -y && sudo apt-get install -y git curl
    fi

    if ! command -v docker &> /dev/null; then
        echo -e "${YELLOW}Docker not found. Installing using the official script...${NC}"
        curl -fsSL https://get.docker.com -o get-docker.sh
        sudo sh get-docker.sh
        rm -f get-docker.sh
        echo -e "${GREEN}Docker installed successfully.${NC}"
        echo -e "${YELLOW}You may need to log out and log back in to run docker without sudo.${NC}"
    fi

    detect_compose_command
    if [ -z "$COMPOSE_CMD" ]; then
        echo -e "${RED}Docker Compose could not be found. Please check your Docker installation.${NC}"
        exit 1
    fi

    if [ ! -d "$PROJECT_DIR" ]; then
        echo -e "${YELLOW}Cloning repository into $PROJECT_DIR...${NC}"
        git clone "$REPO_URL" "$PROJECT_DIR"
    fi

    if [ -f "$CONFIG_FILE" ]; then
        echo -e "\n${RED}WARNING: An existing installation was found.${NC}"
        echo -e "${YELLOW}The 'Install/Reinstall' option performs a CLEAN installation.${NC}"
        echo -e "${RED}This will ERASE your existing rules and settings (config.json).${NC}"

        read -p "Do you want to back up your current config.json and .env first? (y/n): " backup_confirm
        if [[ "$backup_confirm" == "y" || "$backup_confirm" == "Y" ]]; then
            cp "$CONFIG_FILE" "$PROJECT_DIR/config.json.bak.$(date +%Y%m%d-%H%M%S)"
            backup_env_file
            echo -e "${GREEN}Backup completed.${NC}"
        fi

        read -p "Are you sure you want to proceed with a clean reinstallation? (y/n): " reinstall_confirm
        if [[ "$reinstall_confirm" != "y" && "$reinstall_confirm" != "Y" ]]; then
            echo -e "${YELLOW}Reinstallation cancelled.${NC}"
            read -p "Press Enter..."
            return
        fi
    fi

    echo -e "\n${YELLOW}Stopping any existing bot instance...${NC}"
    $COMPOSE_CMD down --remove-orphans

    local original_dir
    original_dir=$(pwd)
    cd "$PROJECT_DIR" || { echo -e "${RED}Failed to enter project directory. Aborting.${NC}"; exit 1; }

    echo -e "\n${YELLOW}--- Core Configuration ---${NC}"
    echo "These values will be stored in .env."
    read -p "Enter your Telegram Bot Token: " bot_token
    read -p "Enter your SUPER ADMIN User ID(s) (comma-separated): " admin_ids

    echo -e "\n${YELLOW}--- DNS Provider Accounts ---${NC}"
    echo "You can add Cloudflare accounts, ArvanCloud accounts, or both."
    echo "You can leave these empty and add accounts later from: Edit Core Configuration > Manage DNS Provider Accounts."

    local cf_accounts_list=""
    while true; do
        read -p "Add a Cloudflare account? (y/n): " add_cf_account
        if [[ "$add_cf_account" != "y" && "$add_cf_account" != "Y" ]]; then break; fi
        read -p "  > Nickname for this Cloudflare account: " cf_nickname
        validate_account_nickname "$cf_nickname" || continue
        read -p "  > Cloudflare API Token: " cf_token
        if [ -z "$cf_accounts_list" ]; then cf_accounts_list="$cf_nickname:$cf_token"; else cf_accounts_list="$cf_accounts_list,$cf_nickname:$cf_token"; fi
    done

    local arvan_accounts_list=""
    while true; do
        read -p "Add an ArvanCloud account? (y/n): " add_arvan_account
        if [[ "$add_arvan_account" != "y" && "$add_arvan_account" != "Y" ]]; then break; fi
        echo -e "${YELLOW}The ArvanCloud Machine User must have access to Domain/DNS management.${NC}"
        read -p "  > Nickname for this ArvanCloud account: " arvan_nickname
        validate_account_nickname "$arvan_nickname" || continue
        read -p "  > ArvanCloud API Key: " arvan_token
        if [ -z "$arvan_accounts_list" ]; then arvan_accounts_list="$arvan_nickname:$arvan_token"; else arvan_accounts_list="$arvan_accounts_list,$arvan_nickname:$arvan_token"; fi
    done

    echo -e "\n${YELLOW}--- Timezone ---${NC}"
    read -p "Enter your timezone [Default: Asia/Tehran]: " user_timezone
    if [ -z "$user_timezone" ]; then user_timezone="Asia/Tehran"; fi

    echo -e "\n${YELLOW}Creating .env file...${NC}"
    cat > "$ENV_FILE" <<EOF
TELEGRAM_BOT_TOKEN=${bot_token}
TELEGRAM_ADMIN_IDS=${admin_ids}
CF_ACCOUNTS=${cf_accounts_list}
ARVAN_ACCOUNTS=${arvan_accounts_list}

TIMEZONE=${user_timezone}
EOF
    echo -e "${GREEN}.env file created successfully.${NC}"

    echo -e "\n${YELLOW}Preparing data files for a clean installation...${NC}"
    rm -f "$CONFIG_FILE" "$PROJECT_DIR/nodes_cache.json" "$PROJECT_DIR/bot_data.pickle"
    touch "$PROJECT_DIR/nodes_cache.json" "$PROJECT_DIR/bot_data.pickle"
    echo '{"notifications":{"enabled":true,"recipients":{"__default__":[]}},"failover_policies":[],"load_balancer_policies":[],"admins":[]}' > "$CONFIG_FILE"
    echo -e "${GREEN}Data files are now clean and ready.${NC}"

    cd "$original_dir"

    echo -e "\n${GREEN}Pulling, building, and starting the bot...${NC}"
    $COMPOSE_CMD pull
    $COMPOSE_CMD up -d --build --remove-orphans

    echo -e "\n${GREEN}--- Installation Complete! ---${NC}"
    echo "The bot is now running in the background."
}

update_env_for_new_versions() {
    if [ ! -f "$ENV_FILE" ]; then
        echo -e "${YELLOW}.env file not found. Skipping .env migration.${NC}"
        return
    fi

    ensure_env_defaults

    echo -e "${GREEN}.env migration check completed.${NC}"
    echo "Cloudflare accounts: $( [ -n "$(read_env_var CF_ACCOUNTS)" ] && echo configured || echo empty )"
    echo "ArvanCloud accounts: $( [ -n "$(read_env_var ARVAN_ACCOUNTS)" ] && echo configured || echo empty )"

    if [ -z "$(read_env_var ARVAN_ACCOUNTS)" ]; then
        echo ""
        read -p "Do you want to add an ArvanCloud account now? (y/n): " add_arvan_now
        if [[ "$add_arvan_now" == "y" || "$add_arvan_now" == "Y" ]]; then
            read -p "  > Nickname for this ArvanCloud account: " arvan_nickname
            if validate_account_nickname "$arvan_nickname"; then
                echo -e "${YELLOW}The ArvanCloud Machine User must have access to Domain/DNS management.${NC}"
                read -p "  > ArvanCloud API Key: " arvan_token
                if [ -n "$arvan_token" ]; then
                    append_account_entry "ARVAN_ACCOUNTS" "$arvan_nickname" "$arvan_token"
                    echo -e "${GREEN}ArvanCloud account added.${NC}"
                fi
            fi
        fi
    fi
}

update_bot() {
    print_header
    if [ ! -d "$PROJECT_DIR" ]; then
        echo -e "${RED}Project directory not found. Please install first.${NC}"
        read -p "Press Enter..."
        return
    fi

    detect_compose_command
    if [ -z "$COMPOSE_CMD" ]; then
        echo -e "${RED}Docker Compose could not be found. Please check your Docker installation.${NC}"
        read -p "Press Enter..."
        return
    fi

    echo -e "${YELLOW}Fetching latest changes from GitHub...${NC}"
    (cd "$PROJECT_DIR" && git pull origin main)
    echo -e "${GREEN}Local repository updated successfully.${NC}"

    echo -e "\n${YELLOW}Checking .env for new required variables...${NC}"
    update_env_for_new_versions

    echo -e "\n${YELLOW}Preparing for a clean restart...${NC}"
    echo "Stopping the current bot instance..."
    $COMPOSE_CMD down

    echo "Cleaning up runtime cache files (your rules in config.json will be preserved)..."
    rm -f "$PROJECT_DIR/bot_data.pickle" "$PROJECT_DIR/nodes_cache.json"
    touch "$PROJECT_DIR/bot_data.pickle" "$PROJECT_DIR/nodes_cache.json"
    echo -e "${GREEN}Cleanup complete.${NC}"

    echo -e "\n${YELLOW}Pulling latest Docker image, rebuilding, and restarting bot...${NC}"
    $COMPOSE_CMD pull
    $COMPOSE_CMD up -d --build --remove-orphans

    echo -e "\n${GREEN}--- Bot has been updated and restarted successfully! ---${NC}"
    echo "Your .env, config.json, rules, and policies have been preserved."
}

main_menu() {
    detect_compose_command

    while true; do
        print_header
        echo "1) Install or Reinstall Bot"
        echo "2) Update Bot from GitHub"
        echo "3) Edit Core Configuration (.env)"
        echo "4) View Live Logs"
        echo "5) Stop Bot"
        echo "6) Start Bot"
        echo "7) Remove Bot Completely"
        echo "8) Exit"
        read -p "Choose an option [1-8]: " choice

        case $choice in
            1) install_bot; read -p "Press Enter..." ;;
            2) update_bot; read -p "Press Enter..." ;;
            3) edit_config ;;
            4)
                if [ -d "$PROJECT_DIR" ] && [ -n "$COMPOSE_CMD" ]; then
                    $COMPOSE_CMD logs -f
                else
                    echo -e "${RED}Project or Docker Compose not found.${NC}"
                    read -p "Press Enter..."
                fi
                ;;
            5)
                if [ -d "$PROJECT_DIR" ] && [ -n "$COMPOSE_CMD" ]; then
                    $COMPOSE_CMD stop
                    echo -e "${YELLOW}Bot stopped.${NC}"
                fi
                read -p "Press Enter..."
                ;;
            6)
                if [ -d "$PROJECT_DIR" ] && [ -n "$COMPOSE_CMD" ]; then
                    $COMPOSE_CMD start
                    echo -e "${GREEN}Bot started.${NC}"
                fi
                read -p "Press Enter..."
                ;;
            7)
                read -p "Are you sure you want to remove the bot completely? (y/n): " confirm_remove
                if [[ "$confirm_remove" == "y" || "$confirm_remove" == "Y" ]]; then
                    if [ -d "$PROJECT_DIR" ] && [ -n "$COMPOSE_CMD" ]; then
                        $COMPOSE_CMD down -v --rmi all
                    fi
                    rm -rf "$PROJECT_DIR"
                    echo -e "${RED}Bot completely removed.${NC}"
                else
                    echo -e "${YELLOW}Removal cancelled.${NC}"
                fi
                read -p "Press Enter..."
                ;;
            8) exit 0 ;;
            *) echo -e "${RED}Invalid option.${NC}"; read -p "Press Enter..." ;;
        esac
    done
}

main_menu
