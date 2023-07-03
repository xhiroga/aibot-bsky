import os
import time
import typing as t
from datetime import datetime

import openai
from atproto import Client
from atproto.xrpc_client import models
from dotenv import load_dotenv

load_dotenv(verbose=True)

HANDLE = os.getenv("HANDLE")
PASSWORD = os.getenv("PASSWORD")

LAST_REPLIED_DATETIME_FILE = "./last_replied_datetime.txt"

openai.organization = os.environ.get("OPENAI_ORGANIZATION")
openai.api_key = os.environ.get("OPENAI_API_KEY")


class OpenAIMessage(t.TypedDict):
    # <https://platform.openai.com/docs/api-reference/chat/create
    role: str
    content: t.Optional[str]
    name: t.Optional[str]
    function_call: t.Optional[t.Dict]


def get_unread_count(client: Client) -> int:
    response = client.bsky.notification.get_unread_count()
    return response.count


def get_notifications(client: Client):
    response = client.bsky.notification.list_notifications()
    return response.notifications


def update_seen(client: Client, seenAt: str = datetime.now().isoformat()):
    response = client.bsky.notification.update_seen({"seenAt": seenAt})
    return


def filter_mentions_and_replies_from_notifications(ns: t.List['models.AppBskyNotificationListNotifications.Notification']) -> t.List[models.AppBskyNotificationListNotifications.Notification]:
    return [n for n in ns if n.reason in ("mention", "reply")]


def get_thread(client: Client, uri: str) -> "models.AppBskyFeedDefs.FeedViewPost":
    return client.bsky.feed.get_post_thread({"uri": uri})


# TODO: receive models.AppBskyFeedDefs.ThreadViewPost
def is_already_replied_to(feed_view: models.AppBskyFeedDefs.FeedViewPost, did: str) -> bool:
    replies = feed_view.thread.replies
    if replies is None:
        return False
    else:
        return any([reply.post.author.did == did for reply in replies])


def flatten_posts(thread: "models.AppBskyFeedDefs.ThreadViewPost") -> t.List[t.Dict[str, any]]:
    posts = [thread.post]

    # recursive case: if there is a parent, extend the list with posts from the parent
    parent = thread.parent
    if parent is not None:
        posts.extend(flatten_posts(parent))

    return posts


def get_oepnai_chat_message_name(name: str) -> str:
    # should be '^[a-zA-Z0-9_-]{1,64}$'
    return name.replace(".", "_")


def posts_to_sorted_messages(posts: t.List[models.AppBskyFeedDefs.PostView], assistant_did: str) -> t.List[OpenAIMessage]:
    sorted_posts = sorted(posts, key=lambda post: post.indexedAt)
    messages = []
    for post in sorted_posts:
        role = "assistant" if post.author.did == assistant_did else "user"
        messages.append(OpenAIMessage(role=role, content=post.record.text, name=get_oepnai_chat_message_name(post.author.handle)))
    return messages


def thread_to_messages(thread: "models.AppBskyFeedGetPostThread.Response", did: str) -> t.List[OpenAIMessage]:
    if thread is None:
        return []
    posts = flatten_posts(thread.thread)
    messages = posts_to_sorted_messages(posts, did)
    return messages


def generate_reply(post_messages: t.List[OpenAIMessage]):
    # <https://platform.openai.com/docs/api-reference/chat/create>
    messages = [{"role": "system", "content": "Reply friendly in 280 characters or less. No @mentions."}]
    messages.extend(post_messages)
    chat_completion = openai.ChatCompletion.create(
        model="gpt-4",
        messages=messages,
    )
    first = chat_completion.choices[0]
    return first.message.content


# 返り値の型に自信なし
def reply_to(notification: models.AppBskyNotificationListNotifications.Notification) -> t.Union[models.AppBskyFeedPost.ReplyRef, models.AppBskyFeedDefs.ReplyRef]:
    parent = {
        "cid": notification.cid,
        "uri": notification.uri,
    }
    if notification.record.reply is None:
        return {"root": parent, "parent": parent}
    else:
        return {"root": notification.record.reply.root, "parent": parent}


def main():
    client = Client()
    profile = client.login(HANDLE, PASSWORD)

    unread_count = get_unread_count(client)
    if unread_count == 0:
        print("No unread notifications.")
        return

    ns = filter_mentions_and_replies_from_notifications(get_notifications(client))

    for notification in ns:
        thread = get_thread(client, notification.uri)
        if is_already_replied_to(thread, profile.did):
            print(f"Already replied to {notification.uri}")
            continue

        post_messages = thread_to_messages(thread, profile.did)
        reply = generate_reply(post_messages)
        client.send_post(text=f"{reply}", reply_to=reply_to(notification))

    update_seen(client)


if __name__ == "__main__":
    # TODO: Keep token.
    while True:
        main()
        print("Sleeping for 30 seconds...")
        time.sleep(30)
