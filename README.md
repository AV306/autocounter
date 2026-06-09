# autocounter

> [!WARNING]
> Note that self-bots are against Discord's TOS, and using this project may result in account termination.
>
> This project is provided for educational purposes only. USE AT YOUR OWN RISK!

A "smart" counting self-bot for Discord. Automatically responds to messages in a counting channel with the next correct count.

Can work together with another instance!

## usage

1. install dependencies with ```pip install -r requirements.txt```. (a venv is recommended)
2. create a `.env` file with `DISCORD_TOKEN` and `TARGET_CHANNEL_ID` set with your user account token and ID of the target counting channel respectively. (setting the shell environment variables also works, but isn't recommended as it opens your Discord token to being stolen. <sub>Granted, if someone had a shell on your machine they could just read the .env file... Will think of a solution soon</sub>)
3. start the bot with ```python main.py```.
4. send a count in the channel to start the bot off.
5. enjoy!

Note that the script will exit if the bot sends a wrong count. This shouldn't happen; if it does, submit an issue with the log file.

## how?

First, a definition:
- **counting message.** any message recognised by a counting bot; in practice, a message whose first word delimited by a space evaluates to a number AND has a reaction from a counting bot.

To count, the bot needs to do two things:

1. Respond to a count message with the correct next count,
2. unless two (or more) users are already counting in a channel.

To achieve this, the bot watches for two events: `on_message` and `on_typing`.

### message event

When a message (`on_message`) is received in the target channel, the bot checks for a counting bot reaction on that message and tries to deduce what number the message is sending. If either fails, the bot ignores the message. Both tasks are conducted simultaneously (as possible with async) to minimise the chances of the counting bot reaction being added before the bot begins waiting for it.

If the message has a "correct" reaction from a counting bot, the bot schedules a task to send the next count after a certain delay (to reduce detection). If the message has a "wrong" reaction, the bot does the same but sends "1".

The bot also sends a "typing" status to signal to other users (or bots) that it is going to send a message, like a real user.

### typing event

When a user, who is not the user who last sent a count message, begins typing in the channel (`on_typing`), the bot cancels any scheduled "send count" task to ensure it won't try to count at the same time as another user. The "not the user who last counted" check allows the user who last counted to prepare the next count in their message box without stalling the bot.

After the bot sends a message, it waits for a reaction from the counting bot to make sure that it did not make a mistake. If it receives a "wrong" reaction, it exits. If it times out, it does nothing in the hopes that a real user will handle the situation properly.

## experiments

Two bot instances with mean delay 0.5s and standard deviation 0.05 increased the count in a server from 380 to 5840 over the course of two hours.
