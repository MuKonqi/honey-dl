import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
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
    content = get_content(url, headers, proxies, retries, timeout)

    if content:
        url_path = urlparse(url).path
        filename = os.path.basename(url_path)

        if add_dates:
            filename = f"({date}) {filename}"

        if create_folders:
            folder = os.path.basename(os.path.dirname(url_path))
            path = os.path.join(domain, folder, filename)
        else:
            path = os.path.join(domain, filename)

        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.isfile(path) or force:
            with open(path, "wb") as f:
                f.write(content)
            print(f"📥 Downloaded: {filename}")
        else:
            print(f"⏭️ Skipped (already exists): {filename}")
        return True


def contains_navigator(url: str, navigator: str):
    return bool(re.search(rf"(\?|&|#){navigator}=(\d+)(&|$)", url))


def get_content(url: str, headers: dict, proxies: dict, retries: int, timeout: int):
    i = 0

    while i <= retries:
        try:
            response = requests.get(url, headers=headers, proxies=proxies, timeout=timeout)
            if response.status_code == 200:
                return response.content
            else:
                print(f"❌ Failed to get: {url}, {response.status_code}")
                return False
        except requests.exceptions.Timeout:
            if i == retries:
                print(f"❌ Timed out: {url}")
                return False
            else:
                print(f"🔁 Timed out, retrying ({i + 1}/{retries}): {url}")
            i += 1
        except requests.exceptions.RequestException as e:
            print(f"❌ Request error: {url}, {e}")
            if i == retries:
                return False
            print(f"🔁 Request error, retrying ({i + 1}/{retries}): {url}")
            i += 1


def get_soup(url: str, headers: dict, proxies: dict, retries: int, timeout: int):
    content = get_content(url, headers, proxies, retries, timeout)

    if content:
        try:
            return BeautifulSoup(content, "lxml")
        except FeatureNotFound:
            return BeautifulSoup(content, "html.parser")
    else:
        return False


def handle_page(
    results: list,
    domain: str,
    add_dates: bool,
    create_folders: bool,
    force: bool,
    headers: dict,
    proxies: dict,
    retries: int,
    timeout: int,
    workers: int,
):
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for img in results:
            date = datetime.strptime(img.get("alt"), "%d-%m-%Y").strftime("%Y-%m-%d")
            src = img.get("src")
            executor.submit(
                download_image, date, src, domain, add_dates, create_folders, force, headers, proxies, retries, timeout
            )


def parse_json_callback(ctx, param, value):
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
    help="Add the date (from alt attribute) to filename",
)
@click.option(
    "-c",
    "--create-folders/--no-create-folders",
    default=False,
    help="Create subfolders based on URL path",
)
@click.option("-f", "--force/--no-force", default=False, help="Overwrite existing files")
@click.option(
    "-h",
    "--headers",
    default='{"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"}',
    callback=parse_json_callback,
    help="HTTP headers as JSON string",
)
@click.option(
    "-n",
    "--navigator",
    default="git",
    help="The URL parameter for navigating to different pages"
)
@click.option(
    "-p1",
    "--page-start",
    default=0,
    help="First page to download (0 means no limit)"
)
@click.option(
    "-p2",
    "--page-end",
    default=0,
    help="Last page to download (0 means no limit)"
)
@click.option(
    "-p",
    "--proxies",
    default="{}",
    callback=parse_json_callback,
    help="Proxy settings as JSON",
)
@click.option("-r", "--retries", default=5, type=int, help="Number of retries per request")
@click.option("-t", "--timeout", default=5, type=int, help="Timeout time per request")
@click.option("-w", "--workers", default=30, type=int, help="Number of downloader workers")
@click.argument("url")
def main(
    add_dates: bool,
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
    try:
        domain = urlparse(url).netloc
        os.makedirs(domain, exist_ok=True)
        with open(os.path.join(domain, ".gitignore"), "w") as f:
            f.write("# Automatically created by honey-dl\n")
            f.write("*")

        if contains_navigator(url, navigator):
            page_start = int([param.split("=")[-1] for param in urlparse(url).query.split("&") if param.startswith(navigator)][0])
        else:
            page_start = 1 if page_start == 0 else page_start

        if page_end != 0 and page_end < page_start:
            print("❌ Last page should be bigger or equal to first page.")
            exit(1)

        page = page_start

        while (page <= page_end) or (page_end == 0):
            navigated_url = f"{url}&{navigator}={page}"
            print(f"📖 Processing page: {page}")
            soup = get_soup(navigated_url, headers, proxies, retries, timeout)
            if soup is None:
                print(f"❌ Failed to retrieve page: {navigated_url}")
                break

            if not soup:
                print("✅ Downloading completed!")
                break

            results = soup.find_all("img", class_="img-thumbnail")

            handle_page(
                results, domain, add_dates, create_folders, force, headers, proxies, retries, timeout, workers
            )
            page += 1
    except KeyboardInterrupt:
        print("\n🛑 Interrupted by user. Trying to exit... If it fails, press again.")
        exit(0)


if __name__ == "__main__":
    main()
