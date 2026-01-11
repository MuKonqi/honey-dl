import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urlparse

import click
import requests
from bs4 import BeautifulSoup, FeatureNotFound


def download_image(
    date: str,
    url: str,
    domain: str,
    add_dates: bool,
    create_folders: bool,
    force: bool,
    headers: dict,
    proxies: dict,
    retries: int,
    timeout: int,
):
    """Download and save a image."""

    content = get_content(url, headers, proxies, retries, timeout)  # Get image as bytes

    if content:
        url_path = urlparse(url).path  # Convert the URL to path
        filename = os.path.basename(url_path)  # Extract filename from URL's path

        if add_dates:  # YYYY-mm-dd
            filename = f"({date}) {filename}"

        if create_folders:  # YYYY-mm-dd
            folder = os.path.basename(os.path.dirname(url_path))
            path = os.path.join(domain, folder, filename)
        else:
            path = os.path.join(domain, filename)

        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.isfile(path) or force:
            with open(path, "wb") as f:
                f.write(content)  # Write the image (bytes) to a file
            print(f"📥 Downloaded: {filename}")
            return True, False  # False means the image just downloaded
        else:
            print(f"⏭️ Skipped (already exists): {filename}")
            return True, True  # True means the image already downloaded

    else:
        return False, False  # Second boolean is unimportant here


def get_content(url: str, headers: dict, proxies: dict, retries: int, timeout: int):
    """Get content of a URL and return as bytes."""

    i = 1

    while i <= retries:
        try:
            response = requests.get(url, headers=headers, proxies=proxies, timeout=timeout)
            if response.status_code == 200:  # Means the request was successful
                return response.content
            else:
                print(f"❌ Failed to get: {url}, {response.status_code}")
                return False
        except requests.exceptions.Timeout:  # Prorably because of the host's limites
            if i == retries:
                print(f"❌ Timed out: {url}")
                return False
            else:
                print(f"🔁 Timed out, retrying ({i + 1}/{retries}): {url}")
            i += 1
        except requests.exceptions.RequestException as e:  # Something is wrong with requests
            print(f"❌ Request error: {url}, {e}")
            if i == retries:
                return False
            print(f"🔁 Request error, retrying ({i + 1}/{retries}): {url}")
            i += 1


def parse_json_callback(ctx, param, value):
    """Parses JSON from passed arguments."""

    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError as e:
        raise click.BadParameter(f"❌ Invalid JSON for {param.name}: {e}") from None


@click.command()
@click.option(
    "-a",
    "--add-dates/--no-add-dates",
    default=True,
    help="Add the upload date (YYYY-mm-dd) to filename",
)
@click.option("-b", "--break-number", default=1, help="Break the loop after reaching this many pages (0 for disable)")
@click.option(
    "-c",
    "--create-folders/--no-create-folders",
    default=False,
    help="Create subfolders based on upload date (YYYY-mm-dd)",
)
@click.option("-f", "--force/--no-force", default=False, help="Overwrite existing files (re-download)")
@click.option(
    "-h",
    "--headers",
    default='{"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"}',
    callback=parse_json_callback,
    help="HTTP headers as JSON string",
)
@click.option("-n", "--navigator", default="git", help="The URL parameter/query for navigating to different pages")
@click.option("-p1", "--page-start", default=0, help="First page to download (0 means no limit: 1)")
@click.option("-p2", "--page-end", default=0, help="Last page to download (0 means no limit: until error)")
@click.option(
    "-p",
    "--proxies",
    default="{}",
    callback=parse_json_callback,
    help="Proxies as JSON string",
)
@click.option("-r", "--retries", default=5, type=int, help="Number of retries per request")
@click.option("-t", "--timeout", default=5, type=int, help="Timeout time in seconds for a request")
@click.option("-w", "--workers", default=30, type=int, help="Number of downloader workers (threads)")
@click.argument("url")
def main(
    add_dates: bool,
    break_number: int,
    create_folders: bool,
    force: bool,
    headers: dict,
    navigator: str,
    page_start: int,
    page_end: int,
    proxies: dict,
    retries: int,
    timeout: int,
    workers: int,
    url: str,
):
    """The main function."""
    try:
        domain = urlparse(url).netloc
        os.makedirs(domain, exist_ok=True)

        # For testing, nobody don't want upload that files accidently
        with open(os.path.join(domain, ".gitignore"), "w") as f:
            f.write("# Automatically created by honey-dl\n")
            f.write("*")

        # If the URL contains the navigator
        if bool(re.search(rf"(\?|&|#){navigator}=(\d+)(&|$)", url)):
            param = [param for param in urlparse(url).query.split("&") if param.startswith(navigator)][
                0
            ]  # Get navigator with the value
            page_start = int(param.split("=")[-1])  # Extract start page number

            try:  # Clean the URL from navigator with its previous and next connectors ("&" and "&"")
                url = url.replace(
                    f"{'&' if url[url.find(param) - 1] == '&' else ''}{param}{'&' if url[url.find(param) + len(param)] else '' == '&'}",
                    "",
                )
            except IndexError:  # Clean the URL from navigator with its previous ("&"")
                url = url.replace(f"{'&' if url[url.find(param) - 1] == '&' else ''}{param}", "")

        else:  # 0 means unlimited, so we should start from one
            page_start = 1 if page_start == 0 else page_start

        if page_end != 0 and page_end < page_start:
            print(f"❌ Last page ({page_end}) should be bigger or equal to first page ({page_start}).")
            exit(1)

        page = page_start
        old_pages = 0

        while (page <= page_end or page_end == 0) and (
            old_pages < break_number or break_number == 0
        ):  # 0 means unlimited, so the condition(s) should be True
            navigated_url = f"{url}&{navigator}={page}"  # Set the target URL
            print(f"📖 Processing page: {page}")

            content = get_content(navigated_url, headers, proxies, retries, timeout)  # Get HTML of the URL
            if content:
                try:
                    soup = BeautifulSoup(content, "lxml")  # Try to parse HTML faster
                except FeatureNotFound:
                    soup = BeautifulSoup(content, "html.parser")  # Parse HTML
            else:
                break  # The downloading is prorably finished

            results = soup.find_all("img", class_="img-thumbnail")  # Find all images in HTML
            with ThreadPoolExecutor(max_workers=workers) as executor:
                old_files = 0  # Number of old files
                tasks = []  # The download tasks

                for img in results:
                    date = datetime.strptime(img.get("alt"), "%d-%m-%Y").strftime("%Y-%m-%d")
                    src = img.get("src")
                    tasks.append(
                        executor.submit(
                            download_image,
                            date,
                            src,
                            domain,
                            add_dates,
                            create_folders,
                            force,
                            headers,
                            proxies,
                            retries,
                            timeout,
                        )
                    )

                for future in as_completed(tasks):
                    if future.result()[1]:  # Check the 2nd boolean
                        old_files += 1

                if old_files == len(tasks):  # Means all files are already downloaded in this page
                    old_pages += 1

            page += 1

        print("✅ Downloading completed!")  # Prorably, LOL
    except KeyboardInterrupt:
        print(
            "\n🛑 Interrupted by user. Trying to exit... If it fails, press again."
        )  # I am sure it will fail because of threads
        exit(0)


if __name__ == "__main__":
    main()
