#!/usr/bin/env python3
"""
Inbox Cleaner - Automatically unsubscribe from marketing emails
"""

import os
import pickle
import base64
import re
import argparse
import json
from collections import deque
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from email.mime.text import MIMEText

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from bs4 import BeautifulSoup
import requests


# Gmail API scopes
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/gmail.send'
]

# Cache file for email metadata
CACHE_FILE = 'email_metadata_cache.json'


class InboxCleaner:
    def __init__(self, dry_run: bool = False, recent_days: Optional[int] = None, batch_size: int = 100):
        self.service = None
        self.dry_run = dry_run
        self.recent_days = recent_days
        self.batch_size = batch_size
        self.unsubscribe_count = 0
        self.cache = self._load_cache()

    def _load_cache(self) -> Dict[str, Dict]:
        """Load email metadata cache from disk"""
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Warning: Could not load cache: {e}")
                return {}
        return {}

    def _save_cache(self):
        """Save email metadata cache to disk"""
        try:
            with open(CACHE_FILE, 'w') as f:
                json.dump(self.cache, f)
        except Exception as e:
            print(f"Warning: Could not save cache: {e}")

    def authenticate(self):
        """Authenticate with Gmail API using OAuth 2.0"""
        creds = None

        # Check for existing token
        if os.path.exists('token.pickle'):
            with open('token.pickle', 'rb') as token:
                creds = pickle.load(token)

        # If no valid credentials, let user log in
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                print("Refreshing access token...")
                creds.refresh(Request())
            else:
                if not os.path.exists('credentials.json'):
                    print("ERROR: credentials.json not found!")
                    print("Please follow the setup instructions in README.md")
                    return False

                print("Opening browser for authorization...")
                flow = InstalledAppFlow.from_client_secrets_file(
                    'credentials.json', SCOPES)
                creds = flow.run_local_server(port=0)

            # Save credentials for next run
            with open('token.pickle', 'wb') as token:
                pickle.dump(creds, token)

        try:
            self.service = build('gmail', 'v1', credentials=creds)
            print("✓ Successfully authenticated with Gmail\n")
            return True
        except HttpError as error:
            print(f"ERROR: Failed to build Gmail service: {error}")
            return False

    def search_emails(self, query: str, max_results: Optional[int] = None) -> List[Dict]:
        """Search for emails matching the query with timeout handling. If max_results is None, fetches all matching emails."""
        import time

        messages = []
        page_token = None

        while True:
            # Gmail API max is 500 per page
            page_size = 500 if max_results is None else min(500, max_results - len(messages))

            # Retry logic for timeouts
            max_attempts = 3
            for attempt in range(max_attempts):
                try:
                    results = self.service.users().messages().list(
                        userId='me',
                        q=query,
                        maxResults=page_size,
                        pageToken=page_token
                    ).execute()

                    page_messages = results.get('messages', [])
                    messages.extend(page_messages)
                    break  # Success

                except TimeoutError as error:
                    if attempt < max_attempts - 1:
                        retry_delay = 5 * (attempt + 1)
                        print(f"    Search timed out - retrying in {retry_delay}s (attempt {attempt + 2}/{max_attempts})")
                        time.sleep(retry_delay)
                    else:
                        print(f"    Search failed after {max_attempts} timeout attempts")
                        return messages  # Return what we have so far

                except HttpError as error:
                    print(f"ERROR: Failed to search emails: {error}")
                    return messages  # Return what we have so far

            # Check if we should continue
            page_token = results.get('nextPageToken')
            if not page_token:
                break

            if max_results and len(messages) >= max_results:
                break

        return messages

    def get_email_details(self, msg_id: str) -> Optional[Dict]:
        """Get full email details including body"""
        try:
            message = self.service.users().messages().get(
                userId='me',
                id=msg_id,
                format='full'
            ).execute()
            return message
        except HttpError as error:
            print(f"ERROR: Failed to get email details: {error}")
            return None

    def get_emails_batch(self, msg_ids: List[str], format: str = 'metadata') -> List[Dict]:
        """Fetch emails using Gmail API batch requests with exponential backoff and caching"""
        import time

        emails = {}  # Use dict to avoid duplicates
        failed_msgs = []

        # Check cache first and separate cached vs. needs-fetch
        cached_count = 0
        to_fetch = []

        for msg_id in msg_ids:
            if msg_id in self.cache:
                emails[msg_id] = self.cache[msg_id]
                cached_count += 1
            else:
                to_fetch.append(msg_id)

        if cached_count > 0:
            print(f"  Found {cached_count} emails in cache")

        if not to_fetch:
            print(f"  All {len(msg_ids)} emails found in cache!")
            return list(emails.values())

        print(f"  Fetching {len(to_fetch)} new emails...")

        # Use a queue-based approach: failed items go to back of queue
        pending_queue = deque(to_fetch)
        processing_ids = set()  # Track IDs currently in a batch

        def callback(request_id, response, exception):
            if exception is None:
                emails[response['id']] = response
                # Add to cache
                self.cache[response['id']] = response
                processing_ids.discard(request_id)
            else:
                # Failed - will be added to back of queue
                failed_msgs.append({
                    'id': request_id,
                    'exception': exception
                })

        total_to_fetch = len(msg_ids)
        delay = 0
        batch_num = 0
        batches_since_save = 0

        while pending_queue or processing_ids:
            batch_num += 1

            # Take up to batch_size IDs from front of queue
            batch_ids = []
            for _ in range(min(self.batch_size, len(pending_queue))):
                msg_id = pending_queue.popleft()
                batch_ids.append(msg_id)
                processing_ids.add(msg_id)

            if not batch_ids:
                # No more in queue, but some still processing - wait a bit
                time.sleep(1)
                continue

            # Apply backoff delay if needed
            if delay > 0:
                time.sleep(delay)

            # Clear failed messages for this batch
            failed_msgs.clear()

            # Create batch
            batch = self.service.new_batch_http_request(callback=callback)

            for msg_id in batch_ids:
                batch.add(
                    self.service.users().messages().get(
                        userId='me',
                        id=msg_id,
                        format=format
                    ),
                    request_id=msg_id
                )

            # Execute with timeout handling
            try:
                batch.execute()

                # Add failed IDs to BACK of queue
                for failed_msg in failed_msgs:
                    failed_id = failed_msg['id']
                    processing_ids.discard(failed_id)
                    pending_queue.append(failed_id)  # Back of queue

                # Check if this was a rate limit error
                is_rate_limit = any(
                    'rateLimitExceeded' in str(f.get('exception', '')) or
                    'userRateLimitExceeded' in str(f.get('exception', '')) or
                    '429' in str(f.get('exception', ''))
                    for f in failed_msgs
                )

                if is_rate_limit:
                    delay = min(delay * 2 if delay > 0 else 2, 16)
                    print(f"\n  Rate limit detected - backing off {delay}s")
                else:
                    # Not rate limit - reset delay
                    delay = 0

                progress_pct = int((len(emails) / total_to_fetch) * 100)
                remaining = len(pending_queue) + len(processing_ids)
                print(f"  Batch {batch_num}: {len(emails)}/{total_to_fetch} emails ({progress_pct}%), {remaining} remaining", end='\r')

            except TimeoutError as error:
                print(f"\n  Batch {batch_num} timed out - retrying")
                # Re-add timed out IDs to back of queue
                for msg_id in batch_ids:
                    if msg_id in processing_ids:
                        processing_ids.discard(msg_id)
                        pending_queue.append(msg_id)
                delay = min(delay * 2 if delay > 0 else 2, 16)

            except HttpError as error:
                # Check if it's a rate limit error
                if '429' in str(error) or 'rateLimitExceeded' in str(error):
                    print(f"\n  Batch {batch_num} rate limited - backing off")
                    delay = min(delay * 2 if delay > 0 else 2, 16)
                else:
                    print(f"\n  Batch {batch_num} HTTP error: {error}")
                # Re-add to back of queue
                for msg_id in batch_ids:
                    if msg_id in processing_ids:
                        processing_ids.discard(msg_id)
                        pending_queue.append(msg_id)

            # Save cache every 10 batches to preserve progress
            batches_since_save += 1
            if batches_since_save >= 10:
                self._save_cache()
                batches_since_save = 0

            # Safety check: if we're not making progress, break
            if batch_num > len(to_fetch) * 3:  # More than 3x expected batches
                print(f"\n  Warning: Stuck in retry loop, {len(pending_queue)} emails still failing")
                break

        print()  # New line after progress

        # Final cache save
        if len(to_fetch) > 0:
            self._save_cache()
            print(f"  ✓ Cache saved ({len(emails) - cached_count} new emails added)")

        final_failed = len(msg_ids) - len(emails)
        if final_failed > 0:
            print(f"  Note: {final_failed} emails failed to fetch after retries (likely deleted or inaccessible)")

        return list(emails.values())

    def extract_unsubscribe_link(self, message: Dict) -> Optional[str]:
        """Extract unsubscribe link from email"""
        # Check List-Unsubscribe header first (most reliable)
        headers = message.get('payload', {}).get('headers', [])
        for header in headers:
            if header['name'].lower() == 'list-unsubscribe':
                value = header['value']
                # Extract URL from header
                url_match = re.search(r'<(https?://[^>]+)>', value)
                if url_match:
                    return url_match.group(1)

        # Fall back to parsing email body
        body = self._get_email_body(message)
        if body:
            # Look for unsubscribe links in HTML
            soup = BeautifulSoup(body, 'html.parser')

            # Find links with "unsubscribe" in text or href
            for link in soup.find_all('a', href=True):
                text = link.get_text().lower()
                href = link['href'].lower()
                if 'unsubscribe' in text or 'unsubscribe' in href:
                    return link['href']

        return None

    def _get_email_body(self, message: Dict) -> str:
        """Extract email body from message"""
        payload = message.get('payload', {})
        body = ''

        if 'parts' in payload:
            for part in payload['parts']:
                if part['mimeType'] == 'text/html':
                    data = part['body'].get('data', '')
                    if data:
                        body = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                        break
        else:
            data = payload.get('body', {}).get('data', '')
            if data:
                body = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')

        return body

    def get_email_subject(self, message: Dict) -> str:
        """Extract subject from email"""
        headers = message.get('payload', {}).get('headers', [])
        for header in headers:
            if header['name'].lower() == 'subject':
                return header['value']
        return '(No subject)'

    def get_email_from(self, message: Dict) -> str:
        """Extract sender from email"""
        headers = message.get('payload', {}).get('headers', [])
        for header in headers:
            if header['name'].lower() == 'from':
                return header['value']
        return '(Unknown sender)'

    def extract_email_address(self, from_field: str) -> str:
        """Extract just the email address from 'Name <email@domain.com>' format"""
        # Look for email in angle brackets
        match = re.search(r'<([^>]+)>', from_field)
        if match:
            return match.group(1).lower().strip()
        # If no angle brackets, assume the whole thing is an email
        return from_field.lower().strip()

    def get_email_date(self, message: Dict) -> Optional[int]:
        """Extract timestamp from email (in milliseconds)"""
        return message.get('internalDate')

    def extract_domain(self, email_address: str) -> str:
        """Extract root domain from email address, grouping subdomains together"""
        if '@' in email_address:
            full_domain = email_address.split('@')[1].lower().strip()
        else:
            full_domain = email_address.lower().strip()

        # Split domain into parts
        parts = full_domain.split('.')

        # Handle special multi-part TLDs (e.g., co.uk, com.au, gov.uk)
        multi_part_tlds = {
            'co.uk', 'com.au', 'co.nz', 'co.za', 'com.br', 'com.mx',
            'gov.uk', 'ac.uk', 'org.uk', 'net.uk',
            'co.jp', 'ne.jp', 'or.jp',
            'com.cn', 'net.cn', 'org.cn'
        }

        if len(parts) >= 2:
            # Check if it ends with a multi-part TLD
            potential_tld = '.'.join(parts[-2:])
            if potential_tld in multi_part_tlds:
                # Take last 3 parts for multi-part TLDs (e.g., mail.company.co.uk -> company.co.uk)
                return '.'.join(parts[-3:]) if len(parts) >= 3 else full_domain
            else:
                # Take last 2 parts for regular TLDs (e.g., mail.company.com -> company.com)
                return '.'.join(parts[-2:])

        return full_domain

    def unsubscribe(self, url: str) -> bool:
        """Visit the unsubscribe URL"""
        if self.dry_run:
            print(f"  [DRY RUN] Would visit: {url}")
            return True

        try:
            # Set a reasonable timeout and user agent
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
            }
            response = requests.get(url, headers=headers, timeout=10, allow_redirects=True)

            if response.status_code == 200:
                self.unsubscribe_count += 1
                return True
            else:
                print(f"  Warning: Got status code {response.status_code}")
                return False
        except Exception as e:
            print(f"  Error visiting unsubscribe link: {e}")
            return False

    def delete_emails(self, message_ids: List[str], permanent: bool = False) -> int:
        """Delete or trash emails using batch requests with exponential backoff"""
        import time
        from collections import deque

        if self.dry_run:
            print(f"  [DRY RUN] Would delete {len(message_ids)} emails")
            return len(message_ids)

        # Use a queue-based approach: failed items go to back of queue
        pending_queue = deque(message_ids)  # Queue of IDs that need to be deleted
        processing_ids = set()  # IDs currently in a batch
        deleted_count = 0
        failed_msgs = []

        def callback(request_id, response, exception):
            nonlocal deleted_count
            if exception is None:
                deleted_count += 1
                # Remove from processing set on success
                processing_ids.discard(request_id)
            else:
                # Failed - will be re-queued at the back
                failed_msgs.append({
                    'id': request_id,
                    'exception': exception
                })

        total_to_delete = len(message_ids)
        delay = 0
        batch_num = 0

        print(f"  Deleting {total_to_delete} emails...")

        while pending_queue or processing_ids:
            batch_num += 1

            # Take up to batch_size IDs from front of queue
            batch_ids = []
            for _ in range(min(self.batch_size, len(pending_queue))):
                msg_id = pending_queue.popleft()
                batch_ids.append(msg_id)
                processing_ids.add(msg_id)

            if not batch_ids:
                # Nothing to process, but still waiting on processing_ids
                # This shouldn't happen, but break to be safe
                break

            # Apply backoff delay if needed
            if delay > 0:
                time.sleep(delay)

            # Clear failed messages for this batch
            failed_msgs.clear()

            # Create batch request
            batch = self.service.new_batch_http_request(callback=callback)

            for msg_id in batch_ids:
                if permanent:
                    batch.add(
                        self.service.users().messages().delete(userId='me', id=msg_id),
                        request_id=msg_id
                    )
                else:
                    batch.add(
                        self.service.users().messages().trash(userId='me', id=msg_id),
                        request_id=msg_id
                    )

            # Execute with timeout handling
            try:
                batch.execute()

                # Add failed IDs to BACK of queue
                for failed_msg in failed_msgs:
                    failed_id = failed_msg['id']
                    pending_queue.append(failed_id)
                    processing_ids.discard(failed_id)

                # Only back off on rate limit errors
                is_rate_limit = any(
                    'rateLimitExceeded' in str(f.get('exception', '')) or
                    'userRateLimitExceeded' in str(f.get('exception', '')) or
                    '429' in str(f.get('exception', ''))
                    for f in failed_msgs
                )

                if is_rate_limit:
                    delay = min(delay * 2 if delay > 0 else 2, 16)
                    print(f"\n    Rate limit detected - backing off {delay}s")
                else:
                    delay = 0

                progress_pct = int((deleted_count / total_to_delete) * 100)
                remaining = len(pending_queue) + len(processing_ids)
                print(f"    Batch {batch_num}: {deleted_count}/{total_to_delete} deleted ({progress_pct}%), {remaining} remaining", end='\r')

            except TimeoutError as error:
                # Add all batch IDs back to queue on timeout
                for msg_id in batch_ids:
                    if msg_id in processing_ids:
                        pending_queue.append(msg_id)
                        processing_ids.discard(msg_id)
                print(f"\n    Batch {batch_num} timed out - {len(pending_queue)} emails still pending")
                delay = min(delay * 2 if delay > 0 else 2, 16)

            except HttpError as error:
                # Add all batch IDs back to queue on HTTP error
                for msg_id in batch_ids:
                    if msg_id in processing_ids:
                        pending_queue.append(msg_id)
                        processing_ids.discard(msg_id)
                print(f"\n    Batch {batch_num} HTTP error: {error}")
                delay = min(delay * 2 if delay > 0 else 2, 16)

            # Safety check: if we're not making progress, break
            if batch_num > total_to_delete * 3:  # More than 3x expected batches
                print(f"\n    Warning: Stuck in retry loop, {len(pending_queue)} emails still failing")
                break

        print()  # New line after progress

        final_failed = total_to_delete - deleted_count
        if final_failed > 0:
            print(f"    Note: {final_failed} emails failed to delete")

        return deleted_count

    def scan_inbox(self, interactive: bool = False, auto: bool = False):
        """Scan inbox for marketing emails with unsubscribe links"""
        print("Scanning inbox for marketing emails...")
        print("(This may take a minute)\n")

        # Search for emails in promotions or with unsubscribe links
        queries = [
            'category:promotions',
            'unsubscribe',
            'list-unsubscribe'
        ]

        all_messages = []
        seen_ids = set()

        for query in queries:
            messages = self.search_emails(query, max_results=500)
            for msg in messages:
                if msg['id'] not in seen_ids:
                    all_messages.append(msg)
                    seen_ids.add(msg['id'])

        if not all_messages:
            print("No marketing emails found!")
            return

        print(f"Found {len(all_messages)} potential marketing emails\n")
        print("Fetching email details in batches...\n")

        # Fetch all email details in batches (need full format for unsubscribe links)
        msg_ids = [msg['id'] for msg in all_messages]
        email_details = self.get_emails_batch(msg_ids, format='full')

        print(f"Analyzing {len(email_details)} emails for unsubscribe links...\n")

        subscriptions = []
        for i, details in enumerate(email_details, 1):
            if i % 50 == 0:
                print(f"  Processed {i}/{len(email_details)}...")

            unsubscribe_link = self.extract_unsubscribe_link(details)
            if unsubscribe_link:
                subscriptions.append({
                    'id': details['id'],
                    'from': self.get_email_from(details),
                    'subject': self.get_email_subject(details),
                    'unsubscribe_url': unsubscribe_link,
                    'timestamp': self.get_email_date(details)
                })

        print(f"\n✓ Found {len(subscriptions)} emails with unsubscribe links\n")

        if not subscriptions:
            print("No subscriptions found to unsubscribe from.")
            return

        # Deduplicate by sender email address
        print("Deduplicating by sender email...\n")
        sender_groups = {}
        for sub in subscriptions:
            sender_email = self.extract_email_address(sub['from'])
            if sender_email not in sender_groups:
                sender_groups[sender_email] = []
            sender_groups[sender_email].append(sub)

        # Calculate cutoff timestamp if recent_days is set
        cutoff_timestamp = None
        if self.recent_days:
            cutoff_date = datetime.now() - timedelta(days=self.recent_days)
            cutoff_timestamp = int(cutoff_date.timestamp() * 1000)  # Convert to milliseconds
            print(f"Filtering to senders with emails from the last {self.recent_days} days...\n")

        unique_subscriptions = []
        filtered_count = 0
        for sender_email, emails in sender_groups.items():
            # Find the most recent email timestamp
            timestamps = [int(email['timestamp']) for email in emails if email.get('timestamp')]
            if not timestamps:
                continue

            most_recent_timestamp = max(timestamps)

            # Filter by recent_days if specified
            if cutoff_timestamp and most_recent_timestamp < cutoff_timestamp:
                filtered_count += 1
                continue

            # Find the most recent email for this sender
            most_recent_email = max(emails, key=lambda e: int(e.get('timestamp', 0)))

            unique_subscriptions.append({
                'sender_email': sender_email,
                'sender_display': emails[0]['from'],  # Keep original display name
                'unsubscribe_url': most_recent_email['unsubscribe_url'],
                'count': len(emails),
                'emails': emails,
                'last_email_timestamp': most_recent_timestamp
            })

        # Sort by most recent email first
        unique_subscriptions.sort(key=lambda x: x['last_email_timestamp'], reverse=True)

        if filtered_count > 0:
            print(f"✓ Filtered out {filtered_count} senders with no recent emails\n")

        print(f"✓ Found {len(unique_subscriptions)} unique senders (from {len(subscriptions)} emails)\n")

        # Display results
        print("=" * 80)
        for i, sub in enumerate(unique_subscriptions, 1):
            # Format last email date
            last_date = datetime.fromtimestamp(sub['last_email_timestamp'] / 1000)
            days_ago = (datetime.now() - last_date).days

            print(f"\n{i}. From: {sub['sender_display']}")
            print(f"   Email count: {sub['count']}")
            print(f"   Last email: {last_date.strftime('%Y-%m-%d')} ({days_ago} days ago)")

            # Show a few example subjects
            example_subjects = [email['subject'][:60] for email in sub['emails'][:2]]
            for subject in example_subjects:
                print(f"   Example: {subject}")

            print(f"   Unsubscribe: {sub['unsubscribe_url'][:70]}...")

            if auto:
                print("   → Unsubscribing...")
                self.unsubscribe(sub['unsubscribe_url'])
            elif interactive:
                response = input("   Unsubscribe? (y/n/q to quit): ").lower()
                if response == 'y':
                    print("   → Unsubscribing...")
                    self.unsubscribe(sub['unsubscribe_url'])
                elif response == 'q':
                    break

        print("\n" + "=" * 80)
        if auto or interactive:
            if self.dry_run:
                print(f"\n[DRY RUN] Would have unsubscribed from {self.unsubscribe_count} senders")
            else:
                print(f"\n✓ Successfully processed {self.unsubscribe_count} unsubscribe requests")
        print(f"\nTotal unique senders: {len(unique_subscriptions)}")
        print(f"Total emails affected: {len(subscriptions)}")

    def cleanup_old_emails(self, older_than_days: int = 90, permanent: bool = False):
        """Interactively delete old marketing emails by sender"""
        print(f"Scanning for marketing emails older than {older_than_days} days...")
        print("(This may take a minute)\n")

        # Calculate cutoff date
        cutoff_date = datetime.now() - timedelta(days=older_than_days)
        cutoff_timestamp = int(cutoff_date.timestamp())

        # Search for old marketing emails - cast a wide net
        queries = [
            # All promotions (not just inbox)
            f'category:promotions older_than:{older_than_days}d',
            # Emails with unsubscribe links anywhere
            f'older_than:{older_than_days}d (unsubscribe OR list-unsubscribe)',
            # Common marketing keywords
            f'older_than:{older_than_days}d (newsletter OR promotional OR "special offer" OR "click here")',
            # Updates and notifications categories
            f'category:updates older_than:{older_than_days}d',
        ]

        all_messages = []
        seen_ids = set()

        print("PHASE 1: Searching for marketing emails")
        print("-" * 50)

        for i, query in enumerate(queries, 1):
            print(f"  Query {i}/{len(queries)}: Searching...")
            messages = self.search_emails(query, max_results=None)  # Fetch ALL results

            new_count = 0
            for msg in messages:
                if msg['id'] not in seen_ids:
                    all_messages.append(msg)
                    seen_ids.add(msg['id'])
                    new_count += 1

            print(f"    → Found {len(messages)} emails ({new_count} new, {len(messages) - new_count} duplicates)")
            print(f"    → Running total: {len(all_messages)} unique emails")

        if not all_messages:
            print(f"\nNo marketing emails found older than {older_than_days} days!")
            return

        print(f"\n✓ Search complete: {len(all_messages)} unique marketing emails found")
        print("\nPHASE 2: Fetching email details")
        print("-" * 50)

        # Fetch all email details using metadata format (much faster than full)
        msg_ids = [msg['id'] for msg in all_messages]
        email_details = self.get_emails_batch(msg_ids, format='metadata')

        print(f"\n✓ Successfully fetched {len(email_details)} out of {len(msg_ids)} emails")

        print("\nPHASE 3: Grouping by domain")
        print("-" * 50)

        # Group by sender domain (part after @)
        domain_groups = {}
        for i, details in enumerate(email_details, 1):
            if i % 100 == 0:
                print(f"  Processed {i}/{len(email_details)}...")

            sender = self.get_email_from(details)
            sender_email = self.extract_email_address(sender)
            domain = self.extract_domain(sender_email)
            timestamp = self.get_email_date(details)

            if domain not in domain_groups:
                domain_groups[domain] = {
                    'domain': domain,
                    'sender_examples': set(),
                    'emails': []
                }

            # Keep track of example sender addresses for display
            domain_groups[domain]['sender_examples'].add(sender_email)
            domain_groups[domain]['emails'].append({
                'id': details['id'],
                'subject': self.get_email_subject(details),
                'timestamp': int(timestamp) if timestamp else 0,
                'from': sender
            })

        # Create batches grouped by domain
        batches = []
        for domain, data in domain_groups.items():
            # Sort emails by date (oldest first)
            data['emails'].sort(key=lambda x: x['timestamp'])

            # Get example sender addresses for display
            sender_examples = list(data['sender_examples'])[:3]

            batches.append({
                'domain': domain,
                'sender_examples': sender_examples,
                'email_count': len(data['emails']),
                'emails': data['emails'],
                'oldest_date': datetime.fromtimestamp(data['emails'][0]['timestamp'] / 1000) if data['emails'] else None,
                'newest_date': datetime.fromtimestamp(data['emails'][-1]['timestamp'] / 1000) if data['emails'] else None
            })

        # Sort batches by email count (most emails first)
        batches.sort(key=lambda x: x['email_count'], reverse=True)

        total_emails = sum(b['email_count'] for b in batches)

        print(f"\n✓ Found {len(batches)} domains with {total_emails} total emails to review\n")
        print("=" * 80)
        print(f"BATCHES TO REVIEW: {len(batches)}")
        print(f"TOTAL EMAILS: {total_emails}")
        print(f"DELETION MODE: {'PERMANENT' if permanent else 'MOVE TO TRASH'}")
        print("=" * 80)

        input("\nPress Enter to start reviewing batches...")

        # Interactive review - queue deletions instead of executing immediately
        deletion_queue = []  # List of message IDs to delete
        marked_batches = []  # Track which batches were marked for deletion
        skipped_batches = 0

        for i, batch in enumerate(batches, 1):
            print("\n" + "=" * 80)
            print(f"BATCH {i}/{len(batches)}")
            print("=" * 80)
            print(f"\nDomain: {batch['domain']}")
            print(f"Email count: {batch['email_count']}")

            # Show example sender addresses from this domain
            print(f"Example senders:")
            for sender in batch['sender_examples']:
                print(f"  • {sender}")
            if len(batch['sender_examples']) > 3:
                print(f"  ... and more")

            if batch['oldest_date'] and batch['newest_date']:
                print(f"Date range: {batch['oldest_date'].strftime('%Y-%m-%d')} to {batch['newest_date'].strftime('%Y-%m-%d')}")

            # Show example subjects
            example_subjects = [email['subject'][:70] for email in batch['emails'][:3]]
            print(f"\nExample subjects:")
            for subject in example_subjects:
                print(f"  • {subject}")

            if len(batch['emails']) > 3:
                print(f"  ... and {len(batch['emails']) - 3} more")

            # Show queue status
            queue_info = f" [{len(deletion_queue)} emails queued]" if deletion_queue else ""
            print(f"\nAction: Delete {batch['email_count']} emails from @{batch['domain']}?{queue_info}")
            response = input("  (y)es / (n)o / (q)uit / (s)how all subjects / (d)elete queued now: ").lower().strip()

            if response == 'q':
                print("\nStopping review...")
                break
            elif response == 'd':
                if deletion_queue:
                    print(f"\nStarting deletion with {len(deletion_queue)} emails queued...")
                    print(f"Skipping remaining {len(batches) - i} batches")
                else:
                    print("\nNo emails queued yet. Continuing review...")
                    continue
                break
            elif response == 's':
                print(f"\nAll subjects from @{batch['domain']}:")
                for email in batch['emails']:
                    date_str = datetime.fromtimestamp(email['timestamp'] / 1000).strftime('%Y-%m-%d')
                    print(f"  [{date_str}] {email['subject']}")
                response = input(f"\nDelete these {batch['email_count']} emails? (y/n): ").lower().strip()

            if response == 'y':
                print(f"  ✓ Marked for deletion")
                message_ids = [email['id'] for email in batch['emails']]
                deletion_queue.extend(message_ids)
                marked_batches.append(batch['domain'])
            elif response != 'd':  # Don't count as skipped if user chose to delete queued
                print("  Skipped")
                skipped_batches += 1

        # Show deletion summary and execute
        print("\n" + "=" * 80)
        print("DELETION QUEUE SUMMARY")
        print("=" * 80)
        print(f"Marked {len(marked_batches)} domains for deletion")
        print(f"Skipped {skipped_batches} domains")
        print(f"Total emails queued: {len(deletion_queue)}")
        if marked_batches:
            print(f"\nDomains marked for deletion:")
            for domain in marked_batches[:10]:
                print(f"  • @{domain}")
            if len(marked_batches) > 10:
                print(f"  ... and {len(marked_batches) - 10} more")
        print("=" * 80)

        if not deletion_queue:
            print("\nNo emails marked for deletion.")
            return

        if self.dry_run:
            print(f"\n[DRY RUN] Would delete {len(deletion_queue)} emails")
            return

        # Confirm before starting deletion
        action = "PERMANENTLY DELETE" if permanent else "move to trash"
        print(f"\nReady to {action} {len(deletion_queue)} emails.")
        response = input("Proceed with deletion? (yes/no): ").lower().strip()

        if response != 'yes':
            print("Deletion cancelled.")
            return

        # Execute all deletions
        print("\n" + "=" * 80)
        print("STARTING DELETION")
        print("=" * 80)
        deleted_total = self.delete_emails(deletion_queue, permanent=permanent)

        print("\n" + "=" * 80)
        print("CLEANUP SUMMARY")
        print("=" * 80)
        action = "permanently deleted" if permanent else "moved to trash"
        print(f"✓ Successfully {action} {deleted_total} out of {len(deletion_queue)} emails")
        print(f"Reviewed {len(batches)} batches")
        print(f"Marked {len(marked_batches)} batches")
        print(f"Skipped {skipped_batches} batches")
        print("=" * 80)


def main():
    parser = argparse.ArgumentParser(
        description='Automatically unsubscribe from marketing emails in Gmail'
    )
    parser.add_argument(
        '--scan',
        action='store_true',
        help='Scan inbox and list subscriptions (default)'
    )
    parser.add_argument(
        '--interactive',
        action='store_true',
        help='Interactively choose which subscriptions to unsubscribe from'
    )
    parser.add_argument(
        '--auto',
        action='store_true',
        help='Automatically unsubscribe from all found subscriptions'
    )
    parser.add_argument(
        '--cleanup',
        action='store_true',
        help='Interactively delete old marketing emails to free up space'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would happen without making changes'
    )
    parser.add_argument(
        '--recent-days',
        type=int,
        default=None,
        metavar='N',
        help='Only show senders with emails from the last N days (e.g., --recent-days 30)'
    )
    parser.add_argument(
        '--older-than',
        type=int,
        default=90,
        metavar='N',
        help='For cleanup mode: only consider emails older than N days (default: 90)'
    )
    parser.add_argument(
        '--permanent',
        action='store_true',
        help='For cleanup mode: permanently delete instead of moving to trash'
    )
    parser.add_argument(
        '--batch-size',
        type=int,
        default=100,
        metavar='N',
        help='Number of emails to process per batch (default: 100, try lower values like 10-50 if hitting rate limits)'
    )

    args = parser.parse_args()

    # Default to scan mode
    if not (args.scan or args.interactive or args.auto or args.cleanup):
        args.scan = True

    print("\n" + "=" * 80)
    print("INBOX CLEANER - Gmail Unsubscribe Tool")
    print("=" * 80 + "\n")

    if args.dry_run:
        print("[DRY RUN MODE - No changes will be made]\n")

    if args.recent_days:
        print(f"[Filtering to senders with emails from the last {args.recent_days} days]\n")

    cleaner = InboxCleaner(dry_run=args.dry_run, recent_days=args.recent_days, batch_size=args.batch_size)

    if not cleaner.authenticate():
        return

    if args.cleanup:
        # Cleanup mode
        if args.permanent and not args.dry_run:
            print("⚠️  PERMANENT DELETE MODE - Emails will be permanently deleted, not just moved to trash!")
            response = input("Are you sure you want to continue? (yes/no): ")
            if response.lower() != 'yes':
                print("Cancelled.")
                return
            print()

        cleaner.cleanup_old_emails(
            older_than_days=args.older_than,
            permanent=args.permanent
        )
    else:
        # Unsubscribe mode
        if args.auto and not args.dry_run:
            print("⚠️  AUTO MODE - This will automatically unsubscribe from all found emails")
            response = input("Are you sure you want to continue? (yes/no): ")
            if response.lower() != 'yes':
                print("Cancelled.")
                return
            print()

        cleaner.scan_inbox(
            interactive=args.interactive,
            auto=args.auto
        )

    print("\n" + "=" * 80)
    print("Done!")
    print("=" * 80 + "\n")


if __name__ == '__main__':
    main()
