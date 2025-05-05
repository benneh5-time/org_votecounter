# org_votecounter
A python-based GUI vote counter for the Total War forum/Org community

The Org Vote Counter is a graphical user interface (GUI) tool designed to assist with tracking votes in forum-based games, specifically for vBulletin-based mafia games. It helps game admins efficiently gather and count votes cast by players during different phases of the game.

Key Features:

    Game Thread URL Input: Users can input the base URL of the game thread to scrape for votes.

    Player List Management: Players can be added manually or imported via a text file. They can be easily deleted as players die or are subbed out.

    Vote Counting: The tool extracts vote data from posts in the thread when they are formatted in bold and with the "Vote: [player]" or "Unvote:" keywords. It only counts votes cast by and for valid players. The vote counter supports 'fuzzy matching,' to assist with capturing votes on alt/shortened names for players. 

    Post Number Range: Admins can specify the start and end post numbers for each voting phase, i.e. this provides the ability to have segment your votecounts by new dayphases or capture votes within certain periods.

    Real-Time Processing: The tool processes the game thread pages live and outputs BBCode. Once the vote count is processed, the results can be easily copied to the clipboard for quick posting to the game thread.

    Data retention: This tool saves a configuration file that saves after each votecount request and loads on startup. Your game will be remembered if you shut the application down and restart it in the same directory with the configuration file. The tool also saves a list of all the prior posts it has scraped already, so it does not need to reach out and hit the server for pages already processed before.

