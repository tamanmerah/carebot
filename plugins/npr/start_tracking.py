import logging
import re

from scrapers.npr_api import NPRAPIScraper
from util.analytics import GoogleAnalytics
from util.models import Story
from util.slack import SlackTools
from plugins.base import CarebotPlugin

logging.basicConfig()
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

npr_api_scraper = NPRAPIScraper()
slack_tools = SlackTools()

class NPRStartTracking(CarebotPlugin):
    """
    Start tracking a story by asking:
    @carebot Track slug-here http://npr.org/example/...
    """
    START_TRACKING_REGEX = re.compile(ur'[Tt]rack (((\w*-*)+,?)+)')

    # Gruber's URL extraction regex
    # http://daringfireball.net/2010/07/improved_regex_for_matching_urls
    GRUBER_URLINTEXT_PAT = re.compile(ur'(?i)\b((?:https?://|www\d{0,3}[.]|[a-z0-9.\-]+[.][a-z]{2,4}/)(?:[^\s()<>]+|\(([^\s()<>]+|(\([^\s()<>]+\)))*\))+(?:\(([^\s()<>]+|(\([^\s()<>]+\)))*\)|[^\s`!()\[\]{};:\'".,<>?\xab\xbb\u201c\u201d\u2018\u2019]))')

    def get_listeners(self):
        return [
            ['start-tracking', self.START_TRACKING_REGEX, self.respond],
        ]

    def respond(self, message):
        m = re.search(self.START_TRACKING_REGEX, message.body['text'])
        url = re.search(self.GRUBER_URLINTEXT_PAT, message.body['text'])

        if not m:
            return False

        slug = m.group(1)
        url = url.group(1)

        if slug:
            # Check if the slug is in the database.
            try:
                story = Story.select().where(Story.url.contains(url)).get()
                story.slug = slug
                story.save()

                text = "Ok! I'm already tracking `%s`, and I've updated the slug." % url

            except Story.DoesNotExist:
                # If it's not in the database, start tracking it.
                if not url:
                    logger.error("Couldn't find story URL in message %s", message.body['text'])
                    text = "Sorry, I need a story URL to start tracking."
                    return

                details = npr_api_scraper.get_story_details(url)

                if not details:
                    logger.error("Couldn't find story in API for URL %s", url)
                    text = "Sorry, I wasn't able to find that story in the API, so I couldn't start tracking it."
                    return

                # Find out what team we need to save this story to
                channel = slack_tools.get_channel_name(message.body['channel'])
                team = self.config.get_team_for_channel(channel)

                # Create the story
                story = Story.create(name=details['title'],
                                     slug=slug,
                                     date=details['date'],
                                     url=url,
                                     image=details['image'],
                                     team=team
                                    )
                story.save()
                text = "Ok, I've started tracking `%s` on %s. The first stats should arrive in 4 hours or less." % (slug, url)

        else:
            text = "Sorry, I wasn't able to start tracking `%s` right now." % slug

        if text:
            return {
                'text': text
            }

