import tkinter as tk
from tkinter import scrolledtext, messagebox, filedialog, simpledialog
from tkinter import *
import math
import requests
from bs4 import BeautifulSoup
import re
import threading
from fuzzywuzzy import process
import difflib
import os
import json
import customtkinter

CACHE_DIR = 'cache'
base_url = "https://forums.totalwar.org/vb/"
CONFIG_FILE = 'config.json'
player_akas = {}

def extract_thread_key(url: str) -> str:
    match = re.search(r'\.php/([^/?#]+)', url)
    return match.group(1) if match else None

def get_cache_path(thread_key: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{thread_key}.json")

def load_cached_posts(thread_key: str) -> list:
    path = get_cache_path(thread_key)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_posts_to_cache(thread_key: str, posts: list):
    path = get_cache_path(thread_key)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(posts, f, indent=2)

def get_posts_from_page(url):
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')
    
    posts = []
    
    postlist = soup.find('div', id='postlist', class_='postlist restrain')
    if not postlist:
        print("Could not find post list container")
        return []
    
    individual_posts = get_individual_posts(postlist)
    
    for post in individual_posts:
        posts.append(post)
    
    return posts
        
def get_individual_posts(postlist):
    ol = postlist.find('ol', id='posts', class_='posts')
    if not ol:
        print("Could not find ordered list of posts")
        return []

    # Each post is a <li> inside the <ol>
    return ol.find_all('li', id=lambda x: x and x.startswith('post')) 
        

def get_username_from_post(post):
    username_container = post.find('div', class_='username_container')
    if not username_container:
        return None

    username_tag = username_container.find('a', class_=lambda c: c and 'username' in c.split())
    if not username_tag:
        return None

    return username_tag.get_text(strip=True)
   
def get_content_from_post(post):
    content_container = post.find('blockquote', class_='postcontent restore')
    if not content_container:
        return None
    
    for bbcode in content_container.find_all('div', class_='bbcode_container'):
        bbcode.decompose()
        
    content_text = content_container.decode_contents().strip()
    return content_text

def get_post_metadata(post):
    # 1. Get global post ID from the <li> tag's id
    post_id_attr = post.get('id')
    if not post_id_attr or not post_id_attr.startswith('post_'):
        return None

    global_post_id = post_id_attr.replace('post_', '')

    # 2. Get thread-level post number and relative link
    postcounter = post.find('a', class_='postcounter')
    if not postcounter:
        return None

    thread_post_number = postcounter.get_text(strip=True)  # e.g., "#211"
    relative_link = postcounter['href']  # e.g., "showthread.php?...#post2053847320"
    full_link = base_url.rstrip('/') + '/' + relative_link.lstrip('/')

    return {
        'global_post_id': global_post_id,
        'thread_post_number': thread_post_number,
        'link': full_link
    }
    
def extract_vote_from_post_content(content_html, valid_players, player_akas):
    soup = BeautifulSoup(content_html, "html.parser")
    bold_tags = soup.find_all("b")

    last_vote = None
    
    aka_lookup = {}
    for player in valid_players:
        aka_lookup[player.lower()] = player
        for aka in player_akas.get(player, []):
            aka_lookup[aka.lower()] = player
            
    match_pool = list(aka_lookup.keys())

    for b in bold_tags:
        text = b.get_text(separator="\n").strip()  # Treat <br> as newline
        lines = text.splitlines()

        for line in lines:
            cleaned = line.strip().lower()

            if re.match(r'^unvote[:\s]*$', cleaned):
                last_vote = ("UNVOTE", None)
            else:
                match = re.match(r'vote:\s*(.+)', cleaned, re.IGNORECASE)
                if match:
                    voted_raw = match.group(1).strip().lower()
                    if voted_raw == 'unvote':
                        last_vote = ("UNVOTE", None)
                    elif voted_raw == 'sleep':
                        last_vote = ("SLEEP", None)
                    else:
                        result = process.extractOne(voted_raw, match_pool, score_cutoff=70)
                        if result:
                            matched, score = result
                            canonical_name = aka_lookup.get(matched, matched)
                            last_vote = (canonical_name, None)
                        else:
                            last_vote = (match.group(1).strip(), True)

    return last_vote


def get_total_posts_and_pages(thread_url):
    response = requests.get(thread_url)
    soup = BeautifulSoup(response.text, 'html.parser')

    # This selector may need to be adjusted based on their forum structure
    last_page_span = soup.find('span', class_='first_last')
    if last_page_span:
        a_tag = last_page_span.find('a', title=True)
        if a_tag:
            title = a_tag['title']
            # Example: "Last Page - Results 3,031 to 3,041 of 3,041"
            match = re.search(r'of\s+([\d,]+)', title)
            if match:
                total_posts = int(match.group(1).replace(',', ''))
                total_pages = math.ceil(total_posts / 30)
                return total_posts, total_pages

    # Fallback: count how many post elements are on the first page
    postlist = soup.find('div', id='postlist')
    if postlist:
        posts = get_individual_posts(postlist)
        if posts:
            return len(posts), 1  # Could be a one-page thread

    return 0, 0  # Something went wrong

def calculate_page_range(start_post_num, stop_post_num, posts_per_page=30):
    start_page = math.ceil(start_post_num / posts_per_page)
    end_page = math.ceil(stop_post_num / posts_per_page) if stop_post_num else None
    return start_page, end_page

def get_current_votes(thread_url, start_post_num, stop_post_num, valid_players, text_output, day):
    posts_per_page = 30
    start_page, end_page = calculate_page_range(start_post_num, stop_post_num, posts_per_page)

    if end_page is None:
        _, total_pages = get_total_posts_and_pages(thread_url)
        end_page = total_pages

    latest_votes = {}  # voter -> (votee, metadata)
    post_counts = {}
    invalid_votes = []
    
    # === Load and prepare cache ===
    thread_key = extract_thread_key(thread_url)
    cached_posts = load_cached_posts(thread_key)
    cached_post_nums = set(int(p["thread_post_number"].lstrip('#')) for p in cached_posts if "thread_post_number" in p)
    all_posts = cached_posts.copy()
    last_cached_post = max(cached_post_nums) if cached_post_nums else 0
    first_needed_post = max(start_post_num, last_cached_post + 1)
    
    start_page = math.ceil(first_needed_post / 30)
    
    for page_num in range(start_page, end_page + 1):
        page_url = f"{thread_url}/page{page_num}"

        text_output.insert(tk.END, f"Processing page {page_num}...\n")
        text_output.see(tk.END)
        text_output.update()

        posts = get_posts_from_page(page_url)

        for post in posts:
            metadata = get_post_metadata(post)
            if not metadata:
                continue

            post_number_str = metadata['thread_post_number'].lstrip('#')
            if not post_number_str.isdigit():
                continue
            post_number = int(post_number_str)

            if post_number < start_post_num or (stop_post_num and post_number > stop_post_num):
                continue

            # Skip if this post is already in the cache
            if post_number in cached_post_nums:
                continue

            # Save this new post to the working set
            post_data = {
                "thread_post_number": metadata['thread_post_number'],
                "username": get_username_from_post(post),
                "content_html": get_content_from_post(post),
                "link": metadata['link']
            }
            all_posts.append(post_data)
            
    # === Save back to cache ===
    if thread_key:
        save_posts_to_cache(thread_key, all_posts)
        cached_posts = load_cached_posts(thread_key)
        cached_post_nums = set(int(p["thread_post_number"].lstrip('#')) for p in cached_posts if "thread_post_number" in p)
        last_cached_post = max(cached_post_nums) if cached_post_nums else 0
    # === Process all posts now ===
    for post in all_posts:
        try:
            post_number = int(post["thread_post_number"].lstrip('#'))
        except:
            continue

        if post_number < start_post_num or (stop_post_num and post_number > stop_post_num):
            continue

        username = post["username"]
        if username:
            post_counts[username] = post_counts.get(username, 0) + 1
        if not username or username not in valid_players:
            continue

        content_html = post["content_html"]
        if not content_html:
            continue

        vote_result = extract_vote_from_post_content(content_html, valid_players, player_akas)
        if vote_result:
            vote, is_invalid = vote_result

            if not is_invalid and vote in valid_players:
                for user, invalid_vote, link in invalid_votes[:]:
                    if username == user:
                        invalid_votes.remove((user, invalid_vote, link))

                latest_votes[username] = (vote, {"link": post["link"], "thread_post_number": post["thread_post_number"]})
                
            elif not is_invalid and vote.upper() == "SLEEP":
                for user, invalid_vote, link in invalid_votes[:]:
                    if username == user:
                        invalid_votes.remove((user, invalid_vote, link))
                latest_votes[username] = (vote, {"link": post["link"], "thread_post_number": post["thread_post_number"]})

            elif vote.upper() == "UNVOTE":
                latest_votes.pop(username, None)

            elif username not in latest_votes:
                invalid_votes.append((username, vote, post["link"]))

    sorted_votes = sorted(latest_votes.items(), key=lambda item: item[1][1]["thread_post_number"])

    votee_map = {}
    for voter, (votee, metadata) in sorted_votes:
        if votee not in votee_map:
            votee_map[votee] = []
        votee_map[votee].append((voter, metadata["link"], post_counts.get(voter, 1)))

    output_lines = ["[center]:bow: [b][size=4]Turby Org Vote Counter v1.0[/size][/b] :bow:[/center]"]
    output_lines.append(f"[center][i]Day {day} - Votes from post {start_post_num} through {last_cached_post}[/i][/center]\n")
    output_lines.append("[table]")
    output_lines.append("[tr][th]Votes[/th][th]Target[/th][th]Voters (Posts in Phase)[/th][/tr]")

    for votee, voters in sorted(votee_map.items(), key=lambda x: -len(x[1])):
        voter_strs = [f"{voter} ([url={link}]{count}[/url])" for voter, link, count in voters]
        output_lines.append(f"[tr][td]{len(voters)}[/td][td][b]{votee}[/b][/td][td]{', '.join(voter_strs)}[/td][/tr]")

    voting_players = set(latest_votes.keys())
    not_voting = [p for p in valid_players if p not in voting_players]

    if not_voting:
        not_voting_strs = [f"{name} ({post_counts.get(name, 0)})" for name in not_voting]
        output_lines.append(f"[tr][td]{len(not_voting)}[/td][td][b]Not Voting[/b][/td][td]{', '.join(not_voting_strs)}[/td][/tr]")

    if invalid_votes:
        output_lines.append(f"[tr][td]{len(invalid_votes)}[/td][td][color=red][b]Invalid Votes[/b][/color][/td][td]")
        for username, raw_vote, link in invalid_votes:
            count = post_counts.get(username, 0)
            output_lines.append(f"{username} voted [b]{raw_vote}[/b] ([url={link}]{count}[/url])")
        output_lines.append("[/td][/tr]")

    output_lines.append("[/table]")
    return "\n".join(output_lines)

# Build GUI
def run_gui():
    
    def get_current_votes_button():
        def task():
            try:
                start = int(start_entry.get()) if start_entry.get() else 1
                stop = int(end_entry.get()) if end_entry.get() else None
                url = url_entry.get().strip()
                if not url:
                    messagebox.showerror("Error", "Please enter the Game Thread URL.")
                    return
                players = [player_listbox.get(i).strip() for i in range(player_listbox.size()) if player_listbox.get(i).strip()]
                if not players:
                    messagebox.showerror("Error", "Please enter valid player names.")
                    return
                day = day_entry.get()

                # Optional: disable button while loading
                get_votes_button.configure(state="disabled")
                copy_button.configure(state="disabled")
                result_text.delete("1.0", tk.END)
                result_text.insert(tk.END, "Processing...\n")

                votes = get_current_votes(url, start, stop, players, result_text, day)

                result_text.delete("1.0", tk.END)
                result_text.insert(tk.END, votes)
                
                save_config()

            except ValueError:
                messagebox.showerror("Error", "Start and end post numbers must be integers.")
            finally:
                get_votes_button.configure(state="normal")
                copy_button.configure(state="normal")

        threading.Thread(target=task, daemon=True).start()

    def copy_votecount():
        root.clipboard_clear()
        root.clipboard_append(result_text.get("1.0", tk.END))
        root.update()

    def player_aka():
        global player_akas
        selected = player_listbox.curselection()
        if not selected:
            messagebox.showwarning("No selection", "Please select a player to add an AKA for")
            return
        
        if len(selected) > 1:
            messagebox.showwarning("Too many seelctions", "Please select a single player to add an AKA for")
            return
        
        main_name = player_listbox.get(selected[0])
        aka = simpledialog.askstring("Add AKA", f"Enter a nickname for '{main_name}:")
        if aka:
            aka = aka.strip()
            if main_name not in player_akas:
                player_akas[main_name] = []
            if aka not in player_akas[main_name]:
                player_akas[main_name].append(aka)
                messagebox.showinfo("AKA Added", f"'{aka} added as nickname for '{main_name}")
            else:
                messagebox.showinfo("Duplicate AKA", f"'{aka} is already an AKA for '{main_name}")
    
    root = tk.Tk()
    root.title("Org Vote Counter")
    root.geometry("620x350")
    Label_id2 = customtkinter.CTkLabel(
        master=root,
        text="Game Thread URL",
        font=("Arial", 10),
        text_color="#000000",
        height=30,
        width=95,
        corner_radius=0,
        bg_color="#FFFFFF",
        fg_color="#FFFFFF",
        )
    Label_id2.place(x=0, y=0)
    global url_entry 
    url_entry = customtkinter.CTkEntry(
        master=root,
        placeholder_text="https://",
        placeholder_text_color="#454545",
        font=("Arial", 10),
        text_color="#000000",
        height=30,
        width=415,
        border_width=2,
        corner_radius=6,
        border_color="#000000",
        bg_color="#FFFFFF",
        fg_color="#FFFFFF",
        )
    url_entry.place(x=100, y=0)
    
    global player_listbox
    player_listbox = tk.Listbox(
        root,
        width=30,
        height=16,
        selectmode=tk.MULTIPLE,
        exportselection=False,
        )
    player_listbox.place(x=320, y=30)
    
    aka_button = customtkinter.CTkButton(
        master=root,
        text="Player AKA",
        font=("Arial", 10),
        text_color="#000000",
        hover=True,
        hover_color="#949494",
        height=50,
        width=190,
        border_width=2,
        corner_radius=6,
        border_color="#000000",
        bg_color="#FFFFFF",
        fg_color="#F0F0F0",
        command=player_aka
    )
    aka_button.place(x=320, y=295)
    def add_player():
        popup = tk.Toplevel()
        popup.title("Add Player")
        popup.geometry("300x100")
        popup.grab_set()  # Makes the popup modal

        label = tk.Label(popup, text="Enter player name:")
        label.pack(pady=5)

        entry = tk.Entry(popup, width=30)
        entry.pack(pady=5)
        entry.focus()

        def on_submit():
            name = entry.get().strip()
            if name and name not in player_listbox.get(0, tk.END):
                player_listbox.insert(tk.END, name)
            popup.destroy()

        submit_btn = tk.Button(popup, text="Add", command=on_submit)
        submit_btn.pack(pady=5)

        popup.bind("<Return>", lambda event: on_submit())  # Allow pressing Enter
    player_entry = customtkinter.CTkButton(
        master=root,
        text="Add Player",
        font=("Arial", 10),
        text_color="#000000",
        hover=True,
        hover_color="#949494",
        height=110,
        width=95,
        border_width=2,
        corner_radius=6,
        border_color="#000000",
        bg_color="#FFFFFF",
        fg_color="#F0F0F0",
        command=add_player
        )
    player_entry.place(x=520, y=0)
    def delete_selected_player():
        selected = player_listbox.curselection()
        for i in reversed(selected):
            player_listbox.delete(i)
    Button_id16 = customtkinter.CTkButton(
        master=root,
        text="Delete/Kill Player",
        font=("undefined", 10),
        text_color="#000000",
        hover=True,
        hover_color="#949494",
        height=110,
        width=95,
        border_width=2,
        corner_radius=6,
        border_color="#000000",
        bg_color="#FFFFFF",
        fg_color="#F0F0F0",
        command=delete_selected_player
        )
    Button_id16.place(x=520, y=120)
    def import_players():
        popup = customtkinter.CTkToplevel()
        popup.title("Paste Player List")
        popup.geometry("400x300")

        label = customtkinter.CTkLabel(popup, text="Paste one player name per line:")
        label.pack(pady=5)

        text_box = customtkinter.CTkTextbox(popup, height=200)
        text_box.pack(padx=10, pady=5, fill='both', expand=True)

        def submit_players():
            raw_input = text_box.get("1.0", tk.END)
            names = [line.strip() for line in raw_input.splitlines() if line.strip()]
            existing = set(player_listbox.get(0, tk.END))
            for name in names:
                if name not in existing:
                    player_listbox.insert(tk.END, name)
            popup.destroy()

        submit_button = customtkinter.CTkButton(popup, text="Import", command=submit_players)
        submit_button.pack(pady=10)
    Button_id17 = customtkinter.CTkButton(
        master=root,
        text="Import Players",
        font=("Arial", 10),
        text_color="#000000",
        hover=True,
        hover_color="#949494",
        height=110,
        width=95,
        border_width=2,
        corner_radius=6,
        border_color="#000000",
        bg_color="#FFFFFF",
        fg_color="#F0F0F0",
        command=import_players
        )
    Button_id17.place(x=520, y=240)
    
    global start_entry
    start_entry = customtkinter.CTkEntry(
        master=root,
        placeholder_text="Day Start Post # (Optional)",
        placeholder_text_color="#454545",
        font=("Arial", 10),
        text_color="#000000",
        height=30,
        width=195,
        border_width=2,
        corner_radius=6,
        border_color="#000000",
        bg_color="#F0F0F0",
        fg_color="#FFFFFF",
        )
    start_entry.place(x=100, y=30)
    Label_id3 = customtkinter.CTkLabel(
        master=root,
        text="First Post #",
        font=("Arial", 10),
        text_color="#000000",
        height=30,
        width=95,
        corner_radius=0,
        bg_color="#F0F0F0",
        fg_color="#F0F0F0",
        )
    Label_id3.place(x=0, y=30)
    global end_entry
    global player_akas 
    end_entry = customtkinter.CTkEntry(
        master=root,
        placeholder_text="Day Ending Post # (Optional)",
        placeholder_text_color="#454545",
        font=("Arial", 10),
        text_color="#000000",
        height=30,
        width=195,
        border_width=2,
        corner_radius=6,
        border_color="#000000",
        bg_color="#F0F0F0",
        fg_color="#FFFFFF",
        )
    end_entry.place(x=100, y=60)
    Label_id4 = customtkinter.CTkLabel(
        master=root,
        text="Last Post #",
        font=("Arial", 10),
        text_color="#000000",
        height=30,
        width=95,
        corner_radius=0,
        bg_color="#F0F0F0",
        fg_color="#FFFFFF",
        )
    Label_id4.place(x=0, y=60)
    global day_entry
    day_entry = customtkinter.CTkEntry(
        master=root,
        placeholder_text="1",
        placeholder_text_color="#454545",
        font=("Arial", 10),
        text_color="#000000",
        height=30,
        width=195,
        border_width=2,
        corner_radius=6,
        border_color="#000000",
        bg_color="#FFFFFF",
        fg_color="#FFFFFF",
        )
    day_entry.place(x=100, y=90)
    Label_id5 = customtkinter.CTkLabel(
        master=root,
        text="Dayphase",
        font=("Arial", 10),
        text_color="#000000",
        height=30,
        width=95,
        corner_radius=0,
        bg_color="#F0F0F0",
        fg_color="#F0F0F0",
        )
    Label_id5.place(x=0, y=90)
    get_votes_button = customtkinter.CTkButton(
        master=root,
        text="Get Current Votes",
        font=("Arial", 10),
        text_color="#000000",
        hover=True,
        hover_color="#949494",
        height=30,
        width=95,
        border_width=2,
        corner_radius=6,
        border_color="#000000",
        bg_color="#FFFFFF",
        fg_color="#F0F0F0",
        command=get_current_votes_button
        )
    get_votes_button.place(x=110, y=160)

    copy_button = customtkinter.CTkButton(
        master=root,
        text="Copy Votecount",
        font=("undefined", 10),
        text_color="#000000",
        hover=True,
        hover_color="#949494",
        height=30,
        width=95,
        border_width=2,
        corner_radius=6,
        border_color="#000000",
        bg_color="#FFFFFF",
        fg_color="#F0F0F0",
        command=copy_votecount
        )
    copy_button.place(x=210, y=160)
    
    global result_text

    result_text = customtkinter.CTkTextbox(
        master=root,
        font=("Arial", 10),
        text_color="#000000",
        height=150,
        width=305,
        border_width=2,
        corner_radius=6,
        border_color="#000000",
        bg_color="#FFFFFF",
        fg_color="#FFFFFF",
        )
    result_text.place(x=0, y=200)

    def get_player_list():
        return list(player_listbox.get(0, tk.END))

    def select_all(event):
        player_listbox.select_set(0, tk.END)
        
    player_listbox.bind("<Control-a>", select_all)
    player_listbox.bind("<Delete>", lambda event: delete_selected_player())
    player_listbox.focus_set()
    

    def save_config():
        """Save the current settings to a JSON file."""
        config = {
            "game_thread_url": url_entry.get().strip(),
            "first_post": start_entry.get().strip(),
            "last_post": end_entry.get().strip(),
            "dayphase": day_entry.get().strip(),
            "player_list": [player_listbox.get(i) for i in range(player_listbox.size())],
            "player_akas": player_akas
        }
        
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save configuration: {e}")

    def load_config():
        """Load settings from the JSON configuration file."""
        global player_akas
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    config = json.load(f)
                
                # Populate the GUI with the loaded values
                url_entry.insert(0, config.get("game_thread_url", ""))
                start_entry.insert(0, config.get("first_post", ""))
                end_entry.insert(0, config.get("last_post", ""))
                day_entry.insert(0, config.get("dayphase", ""))
                
                # Populate the player list
                for player in config.get("player_list", []):
                    player_listbox.insert(tk.END, player)
                player_akas = config.get("player_akas", {})
            except Exception as e:
                messagebox.showerror("Error", f"Failed to load configuration: {e}")
    
    load_config()

    root.mainloop()

if __name__ == '__main__':
    run_gui()

