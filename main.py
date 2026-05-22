import argparse
import ast
import asyncio
from datetime import datetime
import discord
from enum import Enum
from dotenv import dotenv_values
import inspect
import logging
import math
from math_handlers import MATH_HANDLERS
import simpleeval as seval
import random as rng
from typing import cast, Callable, Final, Union, Self, Optional
import os

UserOrMember = Union[discord.User, discord.Member]

KNOWN_COUNTING_BOT_IDS = set( [
    510016054391734273
] )


LOGFILE_NAME = f"autocounter_{datetime.now().isoformat().replace( ':', '-' )}.log"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(name)s/%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler( LOGFILE_NAME, mode="w" )
    ]
)

async def async_sleep( t ):
    """asyncio.sleep(), but resumes immediately if t == 0"""
    if t != 0:
        await asyncio.sleep( t )

class MultiValueEnum( Enum ):
    @classmethod
    def from_str( cls, value: str ) -> Self:
        for attribute in cls:
            if value in attribute.value:
                return attribute
        raise ValueError( f"No valid enum value found for value: {value}" )

class CountingBotReaction( MultiValueEnum ):
    CORRECT = ["✅", "☑️", "💯"]
    WRONG = ["❌"]
    WARNING = ["⚠️", "⚠"] # Variation selector

class AutocounterClient( discord.Client ):
    def __init__( self, token: str, channel_id: int, args: argparse.Namespace ):
        super().__init__( guild_subscriptions=False )

        # Settings
        self.token: Final[str] = token
        self.channel_id: Final[int] = channel_id
        # self.channel can only be set when ready
        self.delay_time_mean: Final[float] = args.delay_time_mean
        self.delay_time_sd: Final[float] = args.delay_time_sd
        self.typing_time_mean: Final[float] = args.typing_time_mean
        self.typing_time_sd: Final[float] = args.typing_time_sd
        self.math_handler: Final[Optional[Callable[[int], str]]] = MATH_HANDLERS.get( args.math_mode )
        self.math_probability: Final[float] = args.math_probability
        #self.do_fake_counts: Final[bool] = args.do_fake_counts
        self.timeout: Final[float] = args.timeout

        # Utilities
        self.random: Final = rng.Random()
        self.simple_eval = seval.SimpleEval()
        # We actually support a superset of math operations for convenience
        # (countingbot doesn't support the arc* trigo ops, for example)
        # FIXME: security risk?
        self.simple_eval.functions.update( {name: obj for name, obj in inspect.getmembers( math, inspect.isbuiltin )} )
        self.simple_eval.operators[ast.BitXor] = seval.safe_power

        # State
        self.last_count: int = 0
        self.last_counted_by_user_id: Optional[int] = None
        self.current_count_task: Optional[asyncio.Task[None]] = None

#region Utilities
    def get_random_delay( self, delay_time: Optional[float]=None, sd: Optional[float]=None ) -> float:
        if delay_time is None:
            # Use the response delay as the default
            delay_time = self.delay_time_mean
        if sd is None:
            sd = self.delay_time_sd
        return max( 0.01, self.random.gauss( delay_time, sd ) )

    async def get_count_from_message_content( self, content: str ) -> int:
        r"""
        Raises ValueError if the message is not a valid count.

        Counting bots appear to evaluate the first word (delimited by a space) of the message,
        and round it to obtain the count.

        Examples: (current count is 1)
        sqrt(2) -> 1.414... -> correct
        sqrt(1.44) -> 1.2 -> correct
        sqrt(3.61) -> 1.9 -> wrong
        """
        target = content.split( " " )[0]
        try:
            return round( self.simple_eval.eval( target ) )
        except (SyntaxError, TypeError, seval.InvalidExpression):
            raise ValueError( f"Failed to evaluate content: '{target}' (full: '{content}')" )

    async def wait_for_counting_bot_reaction( self, message: discord.Message ) -> CountingBotReaction:
        r"""
        Wait for a counting bot to react to the given message and return the reaction.
        Raises an asyncio.TimeoutError if the timeout (indicated by self.timeout) is exceeded.
        """
        def check( reaction: discord.Reaction, user: UserOrMember ) -> bool:
            return user.id in KNOWN_COUNTING_BOT_IDS and reaction.message.id == message.id
        
        # Raises TimeoutError
        reaction, _ = await self.wait_for( "reaction_add", timeout=self.timeout, check=check )

        if type( reaction.emoji ) is not str:
            # Counting bots should never use custom emojis
            logging.warning( f"Received non-string emoji from counting bot: {reaction.emoji.id}" ) #type: ignore
        return CountingBotReaction.from_str( cast( str, reaction.emoji ) )
    
    async def get_counting_bot_reaction_for_message( self, message: discord.Message ) -> CountingBotReaction:
        r"""
        A wrapper for wait_for_counting_bot_reaction that handles timeout by
        attempting to manually locate a counting bot reaction.
        
        Raises RuntimeError if timeout occurs and a valid reaction could not be found.
        """
        try:
            return await self.wait_for_counting_bot_reaction( message )
        except asyncio.TimeoutError:
            logging.warning( f"Timed out waiting for counting bot reaction for message: ({message.guild.name}) {message.author.name}: {message.content}" )
            # A reaction may have been added before we started waiting; try to find manually
            # for reaction in message.reactions:
            #     async for sender in reaction.users():
            #         if sender.id in KNOWN_COUNTING_BOT_IDS:
            #             # Found one!
            #             if type( reaction.emoji ) is not str:
            #                 logging.error( f"Found non-Unicode counting bot reaction: {reaction.emoji}" )
            #                 exit( EXIT_STATUS_ERROR_GENERIC )
            #             result = CountingBotReaction.from_str( reaction.emoji )
            #             logging.info( f"Manually found counting bot reaction for message: {result}" )
            #             return result
            # else:
            #     # Didn't find one
            raise RuntimeError( f"Failed to find counting bot reaction for message: ({message.guild.name}) {message.author.name}: {message.content}" )

#endregion

#region Message sending stuff
    async def send_count_after_delay( self, count: int ):
        r"""
        Send the specified count, and then check if it was correct.

        Waits for {self.delay_time_mean} s (avg) before sending a "typing" status,
        and then "types" for {self.typing_time_mean} s (avg) before sending the count.
        """
        delay_time = self.get_random_delay( self.delay_time_mean, self.delay_time_sd )
        typing_time = self.get_random_delay( self.typing_time_mean, self.typing_time_sd )
        logging.debug( f"Delaying for {delay_time} s" )
        await async_sleep( delay_time )

        logging.debug( f"Typing for {typing_time} s" )
        async with self.channel.typing():
            await async_sleep( typing_time )
            if self.math_handler is not None and self.random.random() < self.math_probability:
                message = self.math_handler( count )
            else:
                message = str( count )
            try:
                sent_message = await asyncio.wait_for( self.channel.send( message ), self.timeout )
            except asyncio.TimeoutError:
                logging.error( f"Timed out waiting for count to be sent: {count}" )
                return
        
        # Validate
        try:
            result = await self.get_counting_bot_reaction_for_message( sent_message )
        except RuntimeError:
            logging.error( f"Failed to get counting bot reaction to newly sent count, skipping verification: {count}" )
            return

        self.last_counted_by_user_id = cast( discord.User, self.user ).id
        
        if result == CountingBotReaction.WRONG:
            logging.error( f"Sent {count} but it was incorrect!" )
            # This should never happen. Exit to signal that something is seriously wrong
            # and we should probably take a look at the code.
            exit( -1 ) 
        elif result == CountingBotReaction.WARNING:
            logging.warning( f"Received warning for count: {count}" )
            # Do nothing, someone will pick it up for us
        else:
            logging.debug( "Count sent and verified" )
            logging.debug( f"incrementing self.last_count: was {self.last_count}, is now {self.last_count + 1}" )
            self.last_count += 1

    async def schedule_count_task( self, count: int ):
        r"""
        Schedule the specified count to be sent after a delay.
        Does nothing if this bot was the last one to count.
        """
        if self.last_counted_by_user_id == cast( discord.User, self.user ).id:
            logging.warning( f"Attempted to schedule new delayed count task when last counted by us: {count=}" )
        if count > self.last_count:
            self.current_count_task = asyncio.create_task( self.send_count_after_delay( count) )
            logging.debug( f"Scheduled new delayed count task with args: {count=}" )
    
    async def cancel_pending_count_task( self ):
        if self.current_count_task and not self.current_count_task.done():
            self.current_count_task.cancel()
            logging.info( "Cancelled pending count task" )
        else:
            logging.debug( "No pending count task to cancel" )
#endregion
            
#region Event handlers
    async def on_ready( self ):
        # Set channel object
        self.channel = cast( discord.TextChannel, self.get_channel( self.channel_id ) )
        if type( self.channel ) is not discord.TextChannel:
            raise RuntimeError( f"Target channel is unsupported type: {type( self.channel )}" ) 
        # Subscribe only to the guild containing that channel
        self.guild = self.channel.guild
        await self.guild.subscribe( typing=True, member_updates=False, threads=False )
        #logging.debug( f"Subscribed to guild {self.guild.id} ({self.guild.name})" )
        logging.info( f"Logged in as {self.user.name}" )
        logging.info( f"Target channel: {self.channel.name} in {self.guild.name}" )
        logging.info( "Autocounter ready!" )

    async def on_typing( self, channel: discord.abc.Messageable, user: UserOrMember, when: datetime ):
        logging.debug( f"({channel.guild.name}) {user.name} is typing" )
        if channel == self.channel:
            if user.id != self.user.id and user.id != self.last_counted_by_user_id:
                logging.debug( f"{user.name} (not us or last counter) began typing in target channel" )
                # Cancel pending count task; will resume when a new count message comes in
                # Ignores the user who last counted, since that event is likely
                # them preparing the next count and won't lead to a new count message
                await self.cancel_pending_count_task()

    async def on_message( self, message: discord.Message ):
        r"""
        Handle a message received in the counting channel, and decide whether
        to respond with a count.
        """
        #logging.debug( f"({message.guild.name}) {message.author.name}: {message.content}" )
        if message.channel.id != self.channel_id or message.author.id == cast( discord.ClientUser, self.user ).id:
            return

        # Schedule the two tasks concurrently
        try:
            async with asyncio.TaskGroup() as group:
                result_task: asyncio.Task[CountingBotReaction] = group.create_task( self.get_counting_bot_reaction_for_message( message ) )
                count_task: asyncio.Task[int] = group.create_task( self.get_count_from_message_content( message.content ) )
        except ExceptionGroup as group:
            if group.subgroup( RuntimeError ):
                # Raised from get_counting_bot_reaction task
                logging.warning( f"Failed to get counting bot reaction for message, skipping it: {message.author.name}: {message.content}" )
            elif group.subgroup( ValueError ):
                # Raised from get_count task
                logging.warning( "Message does not contain a valid count, skipping" )
            else:
                logging.warning( f"Unexpected exception type raised: {group.exceptions}" )
            return
    
        # Count is correct
        result = result_task.result()
        count = count_task.result()
        logging.debug( f"Received valid count message: {count} ({result})" )
        await self.cancel_pending_count_task()
        
        # Decide how to respond, based on correctness of previous count
        if result == CountingBotReaction.WRONG:
            # Next count should be "1"
            logging.info( "Will send 1 (previous count was wrong)" )
            self.last_count = 0
            await self.schedule_count_task( 1 )
        elif result == CountingBotReaction.WARNING:
            # Ignore
            logging.debug( "No action" )
            pass
        else: # result == CountingBotReaction.CORRECT
            # Send the next number
            self.last_count = count
            self.last_counted_by_user_id = message.author.id
            logging.info( f"Will send {count + 1}" )
            await self.schedule_count_task( count + 1 )
#endregion




#region Main
if __name__ == "__main__":
    parser = argparse.ArgumentParser( description="Autocounter: Discord automatic counting self-bot" )
    #parser.add_argument( "token", help="Discord user token" )
    #parser.add_argument( "channel", help="Channel ID to monitor" )
    parser.add_argument( "--env-file", help="Name of the .env file to read; default '.env'", default=".env" )
    parser.add_argument( "--delay-time", dest="delay_time_mean", help="Mean time before the bot begins responding to a count in seconds", type=float, default=0.5 )
    parser.add_argument( "--delay-time-sd", dest="delay_time_sd", help="Standard deviation of response delay time", type=float, default=0.5 )
    parser.add_argument( "--typing-time", dest="typing_time_mean", help="Mean time for the bot to 'type' a count in seconds", type=float, default=0.2 )
    parser.add_argument( "--typing-time-sd", dest="typing_time_sd", help="Standard deviation of 'typing' time", type=float, default=0.1 )
    parser.add_argument( "--continue-after-mistake", "-k", help="Continue counting even after the bot makes a mistake", action="store_true" )
    #parser.add_argument( "--do-fake-counts", "-f", help="Send fake counts instead of the correct next number (aka jerk mode)", action="store_true" )
    parser.add_argument( "--math-mode", "-m", help="Use mathematical operations for counting", choices=["none", "sixseven"], default="none" )
    parser.add_argument( "--math-probability", help="Probability of sending a math count", type=float, default=1.0 )
    parser.add_argument( "--timeout", "-t", help="Timeout for various operations", type=float, default=10 )
    args = parser.parse_args()

    logging.info( f"Running with flags: {args}" )

    config = dotenv_values( args.env_file )

    token = config.get( "DISCORD_TOKEN", os.getenv( "DISCORD_TOKEN" ) )
    channel_id = config.get( "COUNTING_CHANNEL_ID", os.getenv( "COUNTING_CHANNEL_ID" ) )

    if not token:
        raise RuntimeError( "DISCORD_TOKEN environment variable is not set" )
    
    if not channel_id:
        raise RuntimeError( "COUNTING_CHANNEL_ID environment variable is not set" )
    else:
        channel_id = int( channel_id )
    
    client = AutocounterClient( token, channel_id, args )
    event_loop = asyncio.run( client.start( token ) )
#endregion
