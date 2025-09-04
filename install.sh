#!/bin/bash

# --- Colors for better output ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
PROJECT_DIR_NAME="cloudflare-telegram-bot"
PROJECT_DIR="$SCRIPT_DIR/$PROJECT_DIR_NAME"

if [ ! -d "$PROJECT_DIR" ]; then
    PROJECT_DIR="$PROJECT_DIR_NAME"
fi

REPO_URL="https://github.com/ExPLoSiVe1988/cloudflare-telegram-bot.git"
ENV_FILE="$PROJECT_DIR/.env"
COMPOSE_CMD="docker-compose -f $PROJECT_DIR/docker-compose.yml --project-directory $PROJECT_DIR"


# --- Functions ---
print_header() {
    clear
    echo -e "${GREEN}############################################################${NC}"
    echo -e "${GREEN}##                                                        ##${NC}"
    echo -e "${GREEN} ##              Cloudflare-Telegram-Bot                 ##${NC}"
    echo -e "${GREEN}  ## Intelligent Failover & DNS Manager & LoadBalancing ##${NC}"
    echo -e "${GREEN} ##       Powered by @H_ExPLoSiVe (ExPLoSiVe1988)        ##${NC}"
    echo -e "${GREEN}##                                                        ##${NC}"
    echo -e "${GREEN}############################################################${NC}"
    echo ""
}

read_env_var() {
    if [ -f "$ENV_FILE" ]; then
        grep "^${1}=" "$ENV_FILE" | cut -d'=' -f2-
    fi
}

write_env_var() {
    touch "$ENV_FILE"
    if grep -q "^${1}=" "$ENV_FILE"; then
        sed -i "s|^${1}=.*|${1}=${2}|" "$ENV_FILE"
    else
        echo "${1}=${2}" >> "$ENV_FILE"
    fi
}

prompt_for_restart() {
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

# --- Configuration Management Functions ---
manage_admins() {
    local current_admins=$(read_env_var "TELEGRAM_ADMIN_IDS")
    echo -e "\nCurrent Admins: ${YELLOW}${current_admins:-None}${NC}"
    echo "1) Add Admin ID"
    echo "2) Remove Admin ID"
    echo "3) Back"
    read -p "Choose an option: " choice
    case $choice in
        1)
            read -p "Enter new Admin ID to add: " new_admin
            if ! [[ "$new_admin" =~ ^[0-9]+$ ]]; then echo -e "${RED}Invalid ID. Must be a number.${NC}"; return; fi
            if [[ ",$current_admins," == *",$new_admin,"* ]]; then
                echo -e "${YELLOW}Admin ID $new_admin already exists.${NC}"
            else
                if [ -z "$current_admins" ]; then new_list="$new_admin"; else new_list="$current_admins,$new_admin"; fi
                write_env_var "TELEGRAM_ADMIN_IDS" "$new_list"
                echo -e "${GREEN}Admin $new_admin added.${NC}"
            fi
            ;;
        2)
            read -p "Enter Admin ID to remove: " remove_admin
            if ! [[ "$remove_admin" =~ ^[0-9]+$ ]]; then echo -e "${RED}Invalid ID. Must be a number.${NC}"; return; fi
            new_list=$(echo ",$current_admins," | sed "s/,$remove_admin,/,/" | sed 's/^,//;s/,$//')
            if [ "$new_list" == "$current_admins" ]; then
                echo -e "${RED}Admin ID $remove_admin not found.${NC}"
            else
                write_env_var "TELEGRAM_ADMIN_IDS" "$new_list"
                echo -e "${GREEN}Admin $remove_admin removed.${NC}"
            fi
            ;;
        3) return;;
        *) echo -e "${RED}Invalid option.${NC}";;
    esac
    read -p "Press Enter to continue..."
}

manage_cf_accounts() {
    while true; do
        print_header
        echo -e "${YELLOW}--- Manage Cloudflare Accounts ---${NC}"
        
        current_cf_accounts=$(read_env_var "CF_ACCOUNTS")
        echo "Current Accounts: $current_cf_accounts"
        echo "1) Add a new Cloudflare account"
        echo "2) Remove an existing Cloudflare account"
        echo "3) Back to previous menu"
        read -p "Choose an option: " cf_choice

        case $cf_choice in
            1)
                read -p "  Enter a Nickname for the new account: " cf_nickname
                read -p "  Enter the Cloudflare API Token for this account: " cf_token
                new_entry="$cf_nickname:$cf_token"
                if [ -z "$current_cf_accounts" ]; then
                    write_env_var "CF_ACCOUNTS" "$new_entry"
                else
                    write_env_var "CF_ACCOUNTS" "$current_cf_accounts,$new_entry"
                fi
                echo -e "${GREEN}Account '$cf_nickname' added.${NC}"
                read -p "Press Enter to continue..."
                ;;
            2)
                if [ -z "$current_cf_accounts" ]; then
                    echo -e "${YELLOW}No accounts to remove.${NC}"
                else
                    mapfile -t accounts < <(echo "$current_cf_accounts" | tr ',' '\n')
                    echo "Select an account to remove:"
                    i=1
                    for acc in "${accounts[@]}"; do
                        nickname=$(echo "$acc" | cut -d':' -f1)
                        echo "  $i) $nickname"
                        i=$((i+1))
                    done
                    read -p "Enter the number of the account to remove (or 0 to cancel): " del_choice
                    if [[ "$del_choice" -gt 0 && "$del_choice" -le ${#accounts[@]} ]]; then
                        unset "accounts[$((del_choice-1))]"
                        new_accounts_list=$(IFS=,; echo "${accounts[*]}")
                        write_env_var "CF_ACCOUNTS" "$new_accounts_list"
                        echo -e "${GREEN}Account removed.${NC}"
                    else
                        echo "Removal cancelled."
                    fi
                fi
                read -p "Press Enter to continue..."
                ;;
            3)
                break
                ;;
            *)
                echo -e "${RED}Invalid option.${NC}"
                read -p "Press Enter to continue..."
                ;;
        esac
    done
}

edit_config() {
    if [ ! -d "$PROJECT_DIR" ]; then echo -e "${RED}Project directory not found. Please run Install first.${NC}"; read -p "Press Enter..."; return; fi
    
    local config_changed=false
    while true; do
        print_header
        echo -e "${YELLOW}--- Edit Core Configuration (.env) ---${NC}"
        echo "1) Manage Admins"
        echo "2) Manage Cloudflare Accounts"
        echo "3) Edit Telegram Bot Token"
        echo "4) Back to Main Menu"
        read -p "Choose an option: " choice
        
        case $choice in
            1) manage_admins; config_changed=true;;
            2) manage_cf_accounts; config_changed=true;;
            3)
                current_token=$(read_env_var "TELEGRAM_BOT_TOKEN")
                echo "Current Token: $current_token"
                read -p "Enter the new Telegram Bot Token (leave empty to cancel): " new_bot_token
                if [ -n "$new_bot_token" ]; then
                    write_env_var "TELEGRAM_BOT_TOKEN" "$new_bot_token"
                    echo -e "${GREEN}Bot Token updated.${NC}"
                    config_changed=true
                fi
                read -p "Press Enter to continue..."
                ;;
            4) break;;
            *) echo -e "${RED}Invalid option.${NC}";;
        esac
    done

    if [ "$config_changed" = true ]; then
        prompt_for_restart
    fi
}

# --- Installation and Update Functions ---
install_bot() {
    print_header
    echo -e "${GREEN}Starting Bot Installation/Reinstallation...${NC}"

    if ! command -v git &> /dev/null || ! command -v docker-compose &> /dev/null; then
        echo -e "${RED}Error: git and docker-compose are required. Please install them.${NC}"; exit 1;
    fi

    if [ ! -d "$PROJECT_DIR" ]; then
        echo -e "${YELLOW}Cloning repository...${NC}"; git clone "$REPO_URL";
    fi
    
    echo -e "\n${YELLOW}Stopping any existing bot instance...${NC}"
    $COMPOSE_CMD down

    cd "$PROJECT_DIR" || { echo -e "${RED}Failed to enter project directory. Aborting.${NC}"; exit 1; }
    
    echo -e "\n${YELLOW}--- Core Configuration ---${NC}"
    read -p "Enter your Telegram Bot Token: " bot_token
    read -p "Enter your Telegram Admin User ID(s) (comma-separated): " admin_ids
    
    cf_accounts_list=""
    while true; do
        read -p "Add a Cloudflare account? (y/n): " add_account
        if [[ "$add_account" != "y" && "$add_account" != "Y" ]]; then break; fi
        read -p "  > Nickname for this account: " cf_nickname
        read -p "  > API Token for this account: " cf_token
        if [ -z "$cf_accounts_list" ]; then cf_accounts_list="$cf_nickname:$cf_token"; else cf_accounts_list="$cf_accounts_list,$cf_nickname:$cf_token"; fi
    done

    echo -e "\n${YELLOW}Creating .env file...${NC}"
    > .env
    echo "TELEGRAM_BOT_TOKEN=${bot_token}" >> .env
    echo "TELEGRAM_ADMIN_IDS=${admin_ids}" >> .env
    echo "CF_ACCOUNTS=${cf_accounts_list}" >> .env
    echo -e "${GREEN}.env file created successfully.${NC}"

    echo -e "\n${YELLOW}Preparing data files...${NC}"
    rm -rf config.json nodes_cache.json bot_data.pickle
    touch config.json nodes_cache.json bot_data.pickle
    echo '{"notifications":{"enabled":true,"chat_ids":[]},"failover_policies":[],"load_balancer_policies":[]}' > config.json
    echo -e "${GREEN}Data files are now clean and ready.${NC}"
    
    cd ..

    echo -e "\n${GREEN}Pulling, building, and starting the bot...${NC}"
    $COMPOSE_CMD pull
    $COMPOSE_CMD up -d --build --remove-orphans
    
    echo -e "\n${GREEN}--- Installation Complete! ---${NC}"
    echo "The bot is now running in the background."
}

update_bot() {
    print_header
    if [ ! -d "$PROJECT_DIR" ]; then echo -e "${RED}Project directory not found. Please install first.${NC}"; else
        echo "Fetching latest changes from GitHub..."
        (cd "$PROJECT_DIR" && git pull origin main)
        echo -e "${GREEN}Local repository updated.${NC}"
        
        echo "Pulling latest Docker image and restarting bot..."
        $COMPOSE_CMD pull
        $COMPOSE_CMD up -d --build --remove-orphans
        echo -e "${GREEN}Bot has been updated and restarted!${NC}"
    fi
}

# --- Main Menu ---
main_menu() {
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
            1) install_bot; read -p "Press Enter...";;
            2) update_bot; read -p "Press Enter...";;
            3) edit_config;;
            4) if [ -d "$PROJECT_DIR" ]; then $COMPOSE_CMD logs -f; else echo -e "${RED}Project not found.${NC}"; read -p "Press Enter..."; fi;;
            5) if [ -d "$PROJECT_DIR" ]; then $COMPOSE_CMD stop; echo -e "${YELLOW}Bot stopped.${NC}"; fi; read -p "Press Enter...";;
            6) if [ -d "$PROJECT_DIR" ]; then $COMPOSE_CMD start; echo -e "${GREEN}Bot started.${NC}"; fi; read -p "Press Enter...";;
            7)
                read -p "Are you sure you want to remove the bot completely? (y/n): " confirm_remove
                if [[ "$confirm_remove" == "y" || "$confirm_remove" == "Y" ]]; then
                    if [ -d "$PROJECT_DIR" ]; then $COMPOSE_CMD down -v --rmi all; fi
                    rm -rf "$PROJECT_DIR"
                    echo -e "${RED}Bot completely removed.${NC}"
                else
                    echo -e "${YELLOW}Removal cancelled.${NC}"
                fi
                read -p "Press Enter..."
                ;;
            8) exit 0;;
            *) echo -e "${RED}Invalid option.${NC}"; read -p "Press Enter...";;
        esac
    done
}

# --- Script Execution ---
main_menu
