#!/bin/bash

# --- Colors for better output ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
PROJECT_DIR_NAME="cloudflare-telegram-bot"
PROJECT_DIR="$SCRIPT_DIR"

if [[ "$SCRIPT_DIR" != *"$PROJECT_DIR_NAME"* ]]; then
    PROJECT_DIR="$HOME/$PROJECT_DIR_NAME"
fi

REPO_URL="https://github.com/ExPLoSiVe1988/cloudflare-telegram-bot.git"
ENV_FILE="$PROJECT_DIR/.env"
CONFIG_FILE="$PROJECT_DIR/config.json"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.yml"
COMPOSE_CMD=""

# --- Functions ---
detect_compose_command() {
    if command -v docker &> /dev/null && sudo docker compose version &> /dev/null; then
        COMPOSE_CMD="sudo docker compose -f $COMPOSE_FILE --project-directory $PROJECT_DIR"
        echo -e "${GREEN}Detected modern Docker Compose plugin (V2). Using 'docker compose'.${NC}"
    elif command -v docker-compose &> /dev/null; then
        COMPOSE_CMD="sudo docker-compose -f $COMPOSE_FILE --project-directory $PROJECT_DIR"
        echo -e "${YELLOW}Modern Docker Compose not found. Falling back to legacy 'docker-compose'.${NC}"
    else
        COMPOSE_CMD=""
    fi
}

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
    
    local original_dir=$(pwd)
    cd "$PROJECT_DIR" || { echo -e "${RED}Failed to enter project directory.${NC}"; cd "$original_dir"; return; }
    
    local config_changed=false
    while true; do
        print_header
        echo -e "${YELLOW}--- Edit Core Configuration (.env) ---${NC}"
        echo "1) Manage Super Admins"
        echo "2) Manage Cloudflare Accounts"
        echo "3) Edit Telegram Bot Token"
        echo "4) Back to Main Menu"
        read -p "Choose an option: " choice
        
        case $choice in
            1)
                local current_admins=$(read_env_var "TELEGRAM_ADMIN_IDS")
                echo -e "\nCurrent Super Admins (from .env): ${YELLOW}${current_admins:-None}${NC}"
                echo -e "${YELLOW}These are the main admins with full control. Regular admins are managed inside the bot.${NC}"
                read -p "Enter the new comma-separated list of Super Admin IDs: " new_admins
                write_env_var "TELEGRAM_ADMIN_IDS" "$new_admins"
                echo -e "${GREEN}Super Admins list updated.${NC}"
                config_changed=true
                read -p "Press Enter..."
                ;;
            2)
                manage_cf_accounts
                config_changed=true
                ;;
            3)
                current_token=$(read_env_var "TELEGRAM_BOT_TOKEN")
                echo "Current Token: ${current_token:0:10}..."
                read -p "Enter the new Telegram Bot Token (leave empty to cancel): " new_bot_token
                if [ -n "$new_bot_token" ]; then
                    write_env_var "TELEGRAM_BOT_TOKEN" "$new_bot_token"
                    echo -e "${GREEN}Bot Token updated.${NC}"
                    config_changed=true
                fi
                read -p "Press Enter..."
                ;;
            4) break;;
            *) echo -e "${RED}Invalid option.${NC}";;
        esac
    done

    cd "$original_dir"

    if [ "$config_changed" = true ]; then
        prompt_for_restart
    fi
}

# --- Installation and Update Functions ---
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
        rm get-docker.sh
        echo -e "${GREEN}Docker installed successfully.${NC}"
        echo -e "${YELLOW}You may need to log out and log back in to run docker without sudo.${NC}"
    fi
    
    detect_compose_command
    if [ -z "$COMPOSE_CMD" ]; then
        echo -e "${RED}Docker Compose could not be found. Please check your Docker installation.${NC}"
        exit 1
    fi

    if [ ! -d "$PROJECT_DIR" ]; then
        echo -e "${YELLOW}Cloning repository into $PROJECT_DIR...${NC}"; git clone "$REPO_URL" "$PROJECT_DIR";
    fi
    
    if [ -f "$CONFIG_FILE" ]; then
        echo -e "\n${RED}WARNING: An existing installation was found.${NC}"
        echo -e "${YELLOW}The 'Install/Reinstall' option performs a CLEAN installation.${NC}"
        echo -e "${RED}This will ERASE your existing rules and settings (config.json).${NC}"
        
        read -p "Do you want to back up your current config.json first? (y/n): " backup_confirm
        if [[ "$backup_confirm" == "y" || "$backup_confirm" == "Y" ]]; then
            backup_name="config.json.bak.$(date +%Y%m%d-%H%M%S)"
            cp "$CONFIG_FILE" "$PROJECT_DIR/$backup_name"
            echo -e "${GREEN}Backup created at: $PROJECT_DIR/$backup_name${NC}"
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

    local original_dir=$(pwd)
    cd "$PROJECT_DIR" || { echo -e "${RED}Failed to enter project directory. Aborting.${NC}"; exit 1; }
    
    echo -e "\n${YELLOW}--- Core Configuration ---${NC}"
    echo -e "You are setting up the core configuration. These values are stored in the .env file."
    read -p "Enter your Telegram Bot Token: " bot_token
    read -p "Enter your SUPER ADMIN User ID(s) (comma-separated): " admin_ids
    
    cf_accounts_list=""
    while true; do
        read -p "Add a Cloudflare account? (y/n): " add_account
        if [[ "$add_account" != "y" && "$add_account" != "Y" ]]; then break; fi
        read -p "  > Nickname for this account: " cf_nickname
        read -p "  > API Token for this account: " cf_token
        if [ -z "$cf_accounts_list" ]; then cf_accounts_list="$cf_nickname:$cf_token"; else cf_accounts_list="$cf_accounts_list,$cf_nickname:$cf_token"; fi
    done

    echo -e "\n${YELLOW}Creating .env file...${NC}"
    > "$ENV_FILE"
    echo "TELEGRAM_BOT_TOKEN=${bot_token}" >> "$ENV_FILE"
    echo "TELEGRAM_ADMIN_IDS=${admin_ids}" >> "$ENV_FILE"
    echo "CF_ACCOUNTS=${cf_accounts_list}" >> "$ENV_FILE"
    echo -e "${GREEN}.env file created successfully.${NC}"

    echo -e "\n${YELLOW}Preparing data files for a clean installation...${NC}"
    rm -f "$CONFIG_FILE" "$PROJECT_DIR/nodes_cache.json" "$PROJECT_DIR/bot_data.pickle"
    touch "$PROJECT_DIR/nodes_cache.json" "$PROJECT_DIR/bot_data.pickle"
    echo '{"notifications":{"enabled":true,"chat_ids":[]},"failover_policies":[],"load_balancer_policies":[],"admins":[]}' > "$CONFIG_FILE"
    echo -e "${GREEN}Data files are now clean and ready.${NC}"
    
    cd "$original_dir"

    echo -e "\n${GREEN}Pulling, building, and starting the bot...${NC}"
    $COMPOSE_CMD pull
    $COMPOSE_CMD up -d --build --remove-orphans
    
    echo -e "\n${GREEN}--- Installation Complete! ---${NC}"
    echo "The bot is now running in the background."
}

update_bot() {
    print_header
    if [ ! -d "$PROJECT_DIR" ]; then 
        echo -e "${RED}Project directory not found. Please install first.${NC}"
        read -p "Press Enter..."
        return
    fi

    echo -e "${YELLOW}Fetching latest changes from GitHub...${NC}"
    (cd "$PROJECT_DIR" && git pull origin main)
    echo -e "${GREEN}Local repository updated successfully.${NC}"

    echo -e "\n${YELLOW}Preparing for a clean restart...${NC}"
    
    echo "Stopping the current bot instance..."
    $COMPOSE_CMD down

    echo "Cleaning up incompatible old data files (your rules in config.json will be preserved)..."
    rm -f "$PROJECT_DIR/bot_data.pickle" "$PROJECT_DIR/nodes_cache.json"  
    touch "$PROJECT_DIR/bot_data.pickle" "$PROJECT_DIR/nodes_cache.json"
    echo -e "${GREEN}Cleanup complete.${NC}"

    echo -e "\n${YELLOW}Pulling latest Docker image, rebuilding, and restarting bot...${NC}"
    $COMPOSE_CMD pull
    $COMPOSE_CMD up -d --build --remove-orphans
    
    echo -e "\n${GREEN}--- Bot has been updated and restarted successfully! ---${NC}"
    echo -e "Your settings and rules have been preserved."
}

# --- Main Menu ---
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
