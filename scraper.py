import json
import re
import requests
from bs4 import BeautifulSoup

SITE = "https://analyst-assessment-production.up.railway.app"


def get_soup(path):
    r = requests.get(SITE + path, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def collect_community_links():
    """Walk every page of /communities (following 'Next'), plus the homepage."""
    links, path, pages = [], "/communities", 0
    while path:
        soup = get_soup(path)
        pages += 1
        next_path = None
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("/communities/") and href not in links:
                links.append(href)
            if "next" in a.get_text(strip=True).lower():
                next_path = href
        path = next_path if next_path != path else None
    # Safety net: communities featured only on the homepage
    for a in get_soup("/").find_all("a", href=True):
        if a["href"].startswith("/communities/") and a["href"] not in links:
            print("  (found only on homepage:", a["href"], ")")
            links.append(a["href"])
    print(f"Scanned {pages} directory pages")
    return links


def section(lines, header, stop_headers):
    """Return the lines between a header and the next known header."""
    if header not in lines:
        return []
    out = []
    for line in lines[lines.index(header) + 1:]:
        if line in stop_headers or line.startswith("←"):
            break
        out.append(line)
    return out


def parse_community(path):
    soup = get_soup(path)
    lines = soup.get_text("\n", strip=True).split("\n")
    headers = {"Address", "Care Offerings", "Administrator", "Phone"}

    h1 = soup.find("h1")
    name = h1.get_text(strip=True) if h1 else lines[0].split(" — ")[0]

    addr = section(lines, "Address", headers)
    street = " ".join(addr[:-1]) if len(addr) > 1 else (addr[0] if addr else "")
    city = state = zip_code = ""
    if addr:
        m = re.match(r"(.+),\s*([A-Z]{2})\s+(\d{5})", addr[-1])
        if m:
            city, state, zip_code = m.groups()

    return {
        "slug": path.rsplit("/", 1)[-1],
        "url": SITE + path,
        "name": name,
        "street": street,
        "city": city,
        "state": state,
        "zip": zip_code,
        "care_offerings": section(lines, "Care Offerings", headers),
        "administrator": " ".join(section(lines, "Administrator", headers)),
        "phone": " ".join(section(lines, "Phone", headers)),
    }


def scrape():
    communities = [parse_community(p) for p in collect_community_links()]
    with open("communities.json", "w", encoding="utf-8") as f:
        json.dump(communities, f, indent=2)
    return communities


if __name__ == "__main__":
    results = scrape()
    print(f"Scraped {len(results)} communities\n")
    for c in results:
        problem = "  <-- CHECK" if not (c["street"] and c["zip"]) else ""
        print(f"{c['name']} | {c['street']}, {c['city']} {c['state']} {c['zip']} | "
              f"{', '.join(c['care_offerings'])}{problem}")