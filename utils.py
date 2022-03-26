from __future__ import annotations

import json
import re
import shutil
import typing
from collections import namedtuple

import click
import dateutil.parser
import pytz

from datetime import datetime

if typing.TYPE_CHECKING:
    from pathlib import Path

# Used to produce somewhat structured metadata entries
MetadataEntry = namedtuple("MetadataEntry", ["name", "description"])

# A single entry's content and its path (without metadata)
Entry = namedtuple("Entry", ["text", "file"])

def retrieve_metadata(entry: typing.Dict, local_date: datetime, tag_prefix: str, tags_as_links: bool, status_tags: typing.List) -> typing.List:
    metadata = []
    metadata.append(MetadataEntry(name="Dayone UUID", description=f"`{entry['uuid']}`"))

    # Add raw create datetime adjusted for timezone and identify timezone
    metadata.append(
        MetadataEntry(
            name="Creation Date",
            description=f"{local_date.isoformat(), entry['timeZone']}",
        )
    )

    # Add location
    try:
        location = list(
            filter(
                    None,
                    [entry["location"].get(key) for key in ("placeName","localityName","administrativeArea","country")]
                )
        )
    except KeyError:
        location = None

    if location:
        if tags_as_links:
            location = ["[[" + str(loc) + "]]" for loc in location]
        metadata.append(MetadataEntry(name="Location Name", description=', '.join(location)))

    # Add GPS, not all entries have this
    if "location" in entry and all(
        ["latitude" in entry["location"], "longitude" in entry["location"]]
    ):
        lat = entry["location"]["latitude"]
        lon = entry["location"]["longitude"]

        maps_link = "[Google Maps](https://www.google.com/maps/search/?api=1&query={},{})"

        metadata.append(
            MetadataEntry(
                name="Location",
                description=f"[{lat},{lon}]",
            )
        )
        metadata.append(
            MetadataEntry(
                name="Map",
                description=maps_link.format(lat, lon),
            )
        )

    # Add weather information if present
    if "weather" in entry and all(
        [
            i in entry["weather"]
            for i in ["conditionsDescription", "temperatureCelsius", "windSpeedKPH"]
        ]
    ):
        w = entry["weather"]
        metadata.append(
            MetadataEntry(
                name="Weather",
                description=f"{w['conditionsDescription']}, {round(w['temperatureCelsius'], 1)}°C, {round(w['windSpeedKPH'], 1)} kph wind",
            )
        )

    # Add user activity if present
    if "userActivity" in entry and "stepCount" in entry["userActivity"]:
        metadata.append(
            MetadataEntry(name="Steps", description=entry["userActivity"]["stepCount"])
        )

    tags = []
    if "tags" in entry:
        for tag in entry["tags"]:
            if tags_as_links:
                if status_tags and tag.lower() in status_tags:
                    continue
                new_tag = "[[" + tag.capitalize() + "]]"
            else:
                new_tag = f"{tag_prefix}{tag.lower().replace(' ', '-').replace('---', '-')}"
            tags.append(new_tag)

    status = []
    if status_tags:
        # assert isinstance(status_tags, typing.List), "'status_tags' must be a list"
        for tag in status_tags:
            status.append(f"#{tag.capitalize()}") 
    
    if entry["starred"]:
        if tags_as_links:
            status.append("#\u2b50")
        else:
            tags.append(f"{tag_prefix}\u2B50")

    if tags:
        metadata.append(MetadataEntry(name="Tags", description=", ".join(tags)))

    if status:
        metadata.append(MetadataEntry(name="Status", description=', '.join(status)))

    return metadata

def process_journal(journal: Path, icons: bool, tag_prefix: str, verbose: int, convert_links: bool, tags_as_links: bool, yaml: bool, status_tags: typing.List) -> None:
    if verbose != 0:
        click.echo("Verbose mode enabled. Verbosity level: {}".format(verbose))

    """Converts all entries in the JSON files to markdown files"""
    journal_name = (
        journal.stem.lower()
    )  # name of folder where journal entries will end up in your Obsidian vault
    base_folder = journal.resolve().parent 
    journal_folder = base_folder / journal_name

    # Clean out existing journal folder, otherwise each run creates new files
    if journal_folder.exists():
        if verbose > 0:
            click.echo(f"Deleting existing folder: {journal_folder}")
        shutil.rmtree(journal_folder)

    if verbose > 0:
        click.echo(f"Creating {journal_folder}")
    journal_folder.mkdir()

    if icons:
        if verbose > 0:
            click.echo("Icons are on")
        date_icon = "`fas:CalendarAlt` "
    else:
        if verbose > 0:
            click.echo("Icons are off")
        date_icon = ""  # make 2nd level heading

    if yaml and verbose > 0:
        click.echo("Each entry will have a YAML frontmatter")
        if verbose > 1:
            click.echo("YAML frontmatter will contain 'Location', 'Location Name', and 'Tags'")
    elif verbose > 0:
        click.echo("No YAML frontmatter will be added")

    click.echo(f"Begin processing entries for '{journal.name}'")

    # A list of processed entries
    entries = []

    # Mapping between entries UUIDs and Markdown files
    uuid_to_file = {}

    # A set with all the output filenames
    # it's just a check list of filenames to prevent writing only the latest entry within those with the same date
    output_files = set()

    with open(journal, encoding="utf-8") as json_file:
        data = json.load(json_file)
        for count, entry in enumerate(data["entries"], start=1):
            new_entry = []

            creation_date = dateutil.parser.isoparse(entry["creationDate"])
            local_date = creation_date.astimezone(
                pytz.timezone(entry["timeZone"])
            )  # It's natural to use our local date/time as reference point, not UTC

            # Fetch entry's metadata
            metadata = retrieve_metadata(entry, local_date, tag_prefix, tags_as_links, status_tags)

            # Add some metadata as a YAML front matter
            if yaml:
                new_entry.append("---\n")
                for name, description in metadata:
                    if name in ["Location", "Location Name", "Tags"]:
                        new_entry.append(f"{name.lower().replace(' ', '_')}: {description}\n")
                new_entry.append("---\n\n")

            # Add date as page header, removing time if it's 12 midday as time obviously not read
            new_entry.append(
                "## {icon}{date}\n".format(
                    icon=date_icon,
                    date=local_date.strftime("%A, %-d %B %Y at %H:%M").replace(" at 12:00 PM", "")
                )
            )

            # Add body text if it exists (can have the odd blank entry), after some tidying up
            try:
                new_text = entry["text"].replace("\\", "")
                new_text = new_text.replace("\u2028", "\n")
                new_text = new_text.replace("\u1C6A", "\n\n")
                new_text = new_text.replace("\u200b", "")

                if "photos" in entry:
                    # Correct photo links. First we need to rename them. The filename is the md5 code, not the identifier
                    # subsequently used in the text. Then we can amend the text to match. Will only to rename on first run
                    # through as then, they are all renamed.
                    # Assuming all jpeg extensions.
                    for p in entry["photos"]:
                        image_type = p["type"]
                        original_photo_file = (
                            base_folder
                            / "photos"
                            / f"{p['md5']}.{image_type}"
                        )
                        renamed_photo_file = (
                            base_folder
                            / "photos"
                            / f"{p['identifier']}.{image_type}"
                        )
                        if original_photo_file.exists():
                            if verbose > 1:
                                click.echo(
                                    f"Renaming {original_photo_file} to {renamed_photo_file}"
                                )
                            original_photo_file.rename(renamed_photo_file)

                        # Now to replace the text to point to the file in obsidian
                        new_text = re.sub(
                            r"(\!\[\]\(dayone-moment:\/\/)([A-F0-9]+)(\))",
                            (r"![[\2.%s]]" % image_type),
                            new_text,
                        )

                if "pdfAttachments" in entry:
                    # Correct photo pdf links. Similar to what is done on photos
                    for p in entry["pdfAttachments"]:
                        original_pdf_file = (
                            base_folder / "pdfs" / f"{p['md5']}.pdf"
                        )
                        renamed_pdf_file = (
                            base_folder
                            / "pdfs"
                            / f"{p['identifier']}.pdf"
                        )
                        if original_pdf_file.exists():
                            if verbose > 1:
                                click.echo(
                                    f"Renaming {original_pdf_file} to {renamed_pdf_file}"
                                )
                            original_pdf_file.rename(renamed_pdf_file)

                        # Now to replace the text to point to the file in obsidian
                        new_text = re.sub(
                            r"(\!\[\]\(dayone-moment:\/pdfAttachment\/)([A-F0-9]+)(\))",
                            r"![[\2.pdf]]",
                            new_text,
                        )
                
                # Handle audio attachments as well
                if "audios" in entry:
                    for audio in entry["audios"]:
                        audio_format = "m4a" # AAC files are very often saved with .m4a extension
                        original_audio_file = (
                            base_folder / "audios" / f"{audio['md5']}.{audio_format}"
                        )
                        renamed_audio_file = (
                            base_folder / "audios" / f"{audio['identifier']}.{audio_format}"
                        )
                        if original_audio_file.exists():
                            if verbose > 1:
                                click.echo(f"Renaming {original_audio_file} to {renamed_audio_file}")
                            original_audio_file.rename(renamed_audio_file)
                        
                        new_text = re.sub(
                            r"(\!\[\]\(dayone-moment:\/audio/)([A-F0-9]+)(\))",
                            r"![[\2.{}]]".format(audio_format),
                            new_text
                        )

                new_entry.append(new_text)

            except KeyError:
                pass

            # Start Metadata section

            # newEntry.append( '%%\n' ) # uncomment to hide metadata
            new_entry.append("\n\n---\n")
            new_entry.append("**Metadata**\n")
            for name, description in metadata:
                if name != "Location":
                    new_entry.append(f"- {name}: {description}\n")

            # Save entries organised by year, year-month, year-month-day.md
            year_dir = journal_folder / str(creation_date.year)
            month_dir = year_dir / creation_date.strftime("%Y-%m")

            if not year_dir.exists():
                year_dir.mkdir()

            if not month_dir.is_dir():
                month_dir.mkdir()

            # Target filename to save to. Will be modified if already exists
            file_date_format = local_date.strftime("%Y-%m-%d")
            target_file = month_dir / f"{file_date_format}.md"

            # Here is where we handle multiple entries on the same day. Each goes to it's own file
            if target_file in output_files:
                # File exists, need to find the next in sequence and append alpha character marker
                index = 97  # ASCII a
                target_file = month_dir / f"{file_date_format}{chr(index)}.md"
                while target_file in output_files:
                    index += 1
                    target_file = month_dir / f"{file_date_format}{chr(index)}.md"
            
            output_files.add(target_file)

            # Step 1 to replace dayone internal links to other entries with proper Obsidian [[links]]
            metadata_dict = dict(metadata)
            uuid_to_file[metadata_dict["Dayone UUID"].strip('`')] = target_file.name

            entries.append(Entry(text=''.join(new_entry), file=target_file))
        
        click.echo(f"Complete: {count} entries processed.")

    if convert_links:
        click.echo("Converting Day One internal links to Obsidian (when possible)")

        # Step 2 to replace dayone internal links: we must do a second iteration over entries
        # A replacement function for dayone internal links
        def repl(match: re.Match) -> str:
            link_text, uuid = match.groups()
            if uuid in uuid_to_file:
                return f"[[{uuid_to_file[uuid]}|{link_text}]]"
            return link_text
        
        # The regex to match a dayone internal link: [link_text](dayone://view?EntryId=uuid)
        regex = re.compile(r"\[(.*?)\]\(dayone:\/\/.*?([A-F0-9]+)\)") 
    
    for entry in entries:
        text, target_file = entry
        
        if convert_links:
            text = re.sub(regex, repl, text)
        
        with open(target_file, "w", encoding="utf-8") as fp:
            fp.write(text)

    click.echo(f"Done. Entries have been exported to '{journal_folder}'.")
