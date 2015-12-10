# -*- coding: utf-8 -*-
from __future__ import print_function

import io
import os
import sys
import json
import time
import argparse
import tempfile
import datetime
from math import log
from itertools import chain
from azure.storage.blob import BlobService
from azure.storage.queue import QueueService
from azure.common import AzureMissingResourceHttpError
from progressbar import ProgressBar, Percentage, Bar, ETA, FileTransferSpeed

if sys.version_info[0] == 3:
    from configparser import RawConfigParser
    import urllib.parse
    urlparse = urllib.parse.urlparse
else:
    from ConfigParser import RawConfigParser
    import urlparse
    urlparse = urlparse.urlparse

from functools import wraps
from concurrent.futures import ThreadPoolExecutor

class FutureManager():
    def __init__(self, future, timeout):
        self._future = future
        self._timeout = timeout

    def __getattr__(self, name):
        result = self._wait()
        return result.__getattribute__(name)

    def _wait(self):
        return self._future.result(self._timeout)

def threads(f):
    @wraps(f)
    def wrapped(self, *args, **kwargs):
        if self.pool is None:
            self.pool = ThreadPoolExecutor(self.nr_worker)
        return FutureManager(self.pool.submit(f, *([self] + list(args)), **kwargs), timeout=None)
    return wrapped

class InvalidBlobStorePath(AttributeError):
    pass

class QueueProxy(object):
    # void
    def __init__(self, account_name, account_key, queue_name, pid):
        self.pid = pid
        self.name = queue_name
        self.service = QueueService(account_name=account_name, account_key=account_key)

    # int
    @property
    def count(self):
        queue_metadata = self.service.get_queue_metadata(self.name)
        return queue_metadata['x-ms-approximate-messages-count']

    # bool
    def delete_message(self, message_id, pop_receipt):
        try:
            self.service.delete_message(self.name, message_id, pop_receipt)
            return True
        except AzureMissingResourceHttpError:
            return True
        except Exception as e:
            print("[{}] Error occured: {}".format(self.pid, e))
            return False

    # list<Message>
    def get_message(self, limit=None, dry_run=False, **kwargs):
        penalty = 0.1
        while True:
            try:
                return self.service.get_messages(self.name, numofmessages=32, **kwargs) \
                    if not dry_run \
                    else self.service.peek_messages(self.name, numofmessages=32)
            except Exception as e:
                print("[{}] Error occured: {}".format(self.pid, e))
                time.sleep(penalty)
                penalty *= 2.0

    # Message
    def get_messages(self, limit=None, dry_run=False, **kwargs):
        nr_received = 0
        while (not limit) or (limit and nr_received < limit):
            # Get the next batch of messages.
            messages = self.get_message(limit, dry_run, **kwargs)

            # If it's empty then we finish the current process.
            if not messages:
                break

            # Return every message.
            for message in messages:
                yield message
                nr_received += 1

class Uploader(object):
    # void
    def __init__(self, account_name, account_key, blob_path):
        parsed = urlparse(blob_path.format(**self.get_blob_path_params()))
        self.service = BlobService(account_name, account_key)
        self.schema, self.container, self.blob_path = parsed.scheme, parsed.netloc, parsed.path
        self.pid = os.getpid()
        self.pbar = None

    # dict
    def get_blob_path_params(self):
        current_datetime = datetime.datetime.utcnow()
        return {
            'timestamp': current_datetime.isoformat(),
            'date': current_datetime.date().isoformat(),
            'year': current_datetime.year,
            'month': current_datetime.month,
            'day': current_datetime.day,
            'hour': current_datetime.hour,
            'minute': current_datetime.minute,
            'second': current_datetime.second
        }

    # void
    def show_progress(self, current, total):
        def filesize(n,pow=0,b=1024,u='B',pre=['']+[p+'i'for p in'KMGTPEZY']):
            pow,n=min(int(log(max(n*b**pow,1),b)),len(pre)-1),n*b**pow
            return "%%.%if %%s%%s"%abs(pow%(-pow-1))%(n/b**float(pow),pre[pow],u)

        if self.pbar is None:
            self.pbar = ProgressBar(widgets=[
                    '[{}] Uploading {}'.format(self.pid, filesize(total)),
                    '  ',
                    Percentage(), 
                    ' ',
                    Bar(),
                    ' ', 
                    ETA(),
                    ' ', 
                    FileTransferSpeed(),
                    ' '
                ], 
                maxval=total
            ).start()

        self.pbar.update(current)

    # str
    def get_blob_path(self, filename, full=False):
        blob_path = os.path.join(self.blob_path.strip('/'), os.path.split(filename)[-1])
        if not full:
            return blob_path

        return '{}://{}'.format(self.schema, os.path.join(self.container, blob_path))

    # void
    def upload_file(self, filename):
        self.pbar = None
        self.service.put_block_blob_from_path(self.container, self.get_blob_path(filename), filename, \
            max_connections=5, progress_callback=self.show_progress)
        self.pbar.finish()
        self.pbar = None

class Dumper(object):
    # void
    def __init__(self, account_name, account_key, queue_name, tmpdir, blob_path, nr_approx_records_per_file, visibility_timeout, dry_run, nr_worker, run_once):
        self.pid = os.getpid()
        self.tmpdir = tmpdir
        self.dry_run = dry_run
        self.run_once = run_once
        self.nr_approx_records_per_file = int(nr_approx_records_per_file)
        self.visibility_timeout = int(visibility_timeout)
        self.queue = QueueProxy(account_name, account_key, queue_name, self.pid)
        self.uploader = Uploader(account_name, account_key, blob_path)
        self.receipts = []
        self.nr_worker = int(nr_worker)
        self.pool = None

    # str
    def create_temporary_file(self, suffix):
        with tempfile.NamedTemporaryFile(prefix='queue-dump-', suffix=suffix, dir=self.tmpdir, delete=False) as fd:
            filename = fd.name
            print('[{}] Creating empty file for dumping `{}`'.format(self.pid, filename))
        return filename

    # bool
    def is_queue_empty(self):
        return queue.count == 0

    # type
    def decode_message_text(self, message_text):
        return message_text

    # bool
    def is_filter_message(self, message_text):
        return False

    # tuple<int, int>
    def collect_iteration(self):
        # Empty the stored receipts.
        self.receipts = []

        # Create a progressbar.
        pbar = ProgressBar(widgets=[
            "[{}] Collecting ~{} messages:  ".format(self.pid, self.nr_approx_records_per_file),
            Percentage(), " ", Bar(), " ", ETA(), "  " ], maxval=self.nr_approx_records_per_file).start()

        # Dump messages.
        messages = self.collect_iteration_thread(pbar)
        self.pool.shutdown()
        self.pool = None

        # Remove the progressbar.
        pbar.finish()
        return messages

    # tuple<int, int>
    def dump_iteration(self, filename, messages):
        # Create a progressbar.
        pbar = ProgressBar(widgets=[
            "[{}] Dumping {} messages:  ".format(self.pid, len(messages)),
            Percentage(), " ", Bar(), " ", ETA(), "  " ], maxval=len(messages)).start()

        # Reopen the file with UTF-8 encoding.
        with io.open(filename, 'w', encoding='utf-8') as fd:
            nr_dumped, nr_ignored = 0, 0
            for message in messages:
                # Decode the message's content
                try:
                    decoded_text = self.decode_message_text(message.message_text)
                except:
                    print('[{}] Could not decode: {}'.format(self.pid, message.message_text))
                    nr_ignored += 1
                    continue

                # Filter out not necessary records.
                if self.is_filter_message(decoded_text):
                    continue

                # Craft the information blob.
                data = {
                    u'id': message.message_id, 
                    u'receipt': message.pop_receipt,
                    u'text': decoded_text
                }

                # Write it into the temporary JSON file.
                json_data = json.dumps(data)
                fd.write(json_data.strip() + u"\n")
                fd.flush()

                # Add records into the recipients.
                self.receipts.append((message.message_id, message.pop_receipt))

                # Update the counter and the progress bar
                nr_dumped += 1
                pbar.update(nr_dumped)

        # Remove the progress bar and print the iteration results.
        pbar.finish()
        print('[{}] Dumped {} messages into `{}`, ignored {}'.format(self.pid, nr_dumped, filename, nr_ignored))

        # Returns the dumped and the ignored numbers.
        return nr_dumped, nr_ignored

    # str
    def create_output_file(self, filename):
        return filename
    
    # str
    def convert_file(self, filename):
        output_filename = self.create_output_file(filename)
        print("[{}] Output `{}` file was generated successfully.".format(self.pid, output_filename))
        return output_filename

    # void
    def upload_file_to_blob_storage(self, filename):
        full_blob_path = self.uploader.get_blob_path(filename, full=True)
        print("[{}] Uploading output file into `{}`.".format(self.pid, full_blob_path))
        self.uploader.upload_file(filename)
        print("[{}] Successfully uploaded into Azure Blob Storage. Final path: `{}`".format(self.pid, full_blob_path))

    # list<Message>
    @threads
    def get_messages_thread(self, pbar):
        results = []
        for message in self.queue.get_messages(self.nr_approx_records_per_file/self.nr_worker, \
                dry_run=self.dry_run, visibilitytimeout=self.visibility_timeout):
            pbar.update(min(pbar.value+1, self.nr_approx_records_per_file))
            results.append(message)
        return results

    # list<Message>
    def collect_iteration_thread(self, pbar):
        messages = [ self.get_messages_thread(pbar) for i in range(self.nr_worker)]
        return list(chain.from_iterable(messages))

    # void
    @threads
    def remove_iteration_thread(self, pbar, message_id, pop_receipt):
        is_deleted, nr_success = False, 0
        while not is_deleted:
            is_deleted = self.queue.delete_message(message_id, pop_receipt)
            nr_success += 1

        pbar.update(pbar.value+1)
        return float(nr_success)

    # void
    def remove_iteration(self):
        # Skip if it's a dryrun!
        if self.dry_run:
            return 1.0

        # Create a progressbar.
        pbar = ProgressBar(widgets=[
            "[{}] Deleting {} messages:  ".format(self.pid, len(self.receipts)),
            Percentage(), " ", Bar(), " ", ETA(), "  " ], maxval=len(self.receipts)).start()

        # Removing messages.
        delete_status = [ self.remove_iteration_thread(pbar, message_id, pop_receipt) for message_id, pop_receipt in self.receipts ]
        avg_nr_tries = sum(delete_status)/len(delete_status)
        self.pool.shutdown()
        self.pool = None

        # Remove the progress bar.
        pbar.finish()
        
        # Write statistics.
        print("[{}] Records were deleted sucessfully. Average score: {}".format(self.pid, avg_nr_tries))
        return avg_nr_tries

    # void
    def clear_file(self, filename):
        # Remove if it's a dry_run
        if self.dry_run:
            return

        with io.open(filename, 'w', encoding='utf-8') as fd:
            fd.write(u'')
        print("[{}] File's content was removed. `{}`".format(self.pid, filename))

    # bool
    def run_iteration(self):
        # Create the target outuput JSON file.
        json_filename = self.create_temporary_file('.json')

        # Dump AQS records into the file.
        nr_dumped, nr_ignored = self.dump_iteration(json_filename, self.collect_iteration())
        if nr_dumped == 0:
            return True

        # Convert the JSON file into something else.
        output_filename = self.convert_file(json_filename)

        # Upload the output file into Blob Storage.
        self.upload_file_to_blob_storage(output_filename)

        # Remove from Azure Queue Storage.
        self.remove_iteration()

        # Clear the file's content. (only remove after the dump is finished)
        for filename in set([json_filename, output_filename]):
            self.clear_file(filename)

        return False

    # void
    def run(self):
        print("[{}] Initializing the dumping process for `{}` queue. Record count: {}".format(self.pid, self.queue.name, self.queue.count))
        if self.run_once:
            self.run_iteration()
        else:
            while not self.run_iteration(): pass
        print("[{}] Finishing the dumping process for `{}` queue. Record count: {}".format(self.pid, self.queue.name, self.queue.count))

# dict
def get_config_dict():
    # Set up current directory.
    current_directory = os.path.dirname(os.path.abspath(__file__))
    
    # Define arguments for the dumper.
    parser = argparse.ArgumentParser(description='Dumps the content of Azure Queue Storage into files.')
    parser.add_argument('-c', '--config', type=argparse.FileType('r'), help='Configuration file.')
    parser.add_argument('-a', '--account', help="Azure Queue Storage's account name.")
    parser.add_argument('-k', '--key', help="Azure Queue Storage's account key")
    parser.add_argument('-q', '--queue', help="Azure Queue Storages queue name")
    parser.add_argument('-p', '--blobpath', help="Azure Blob Storage's path to store the final files.")
    parser.add_argument('-d', '--tmpdir', help="Target directory. Default: current directory")
    parser.add_argument('-n', '--number', help="Approx records per file. Default: 10000")
    parser.add_argument('-v', '--visibility', help="Visibility timeout after get the message. Default: 600s")
    parser.add_argument('-w', '--worker', help="Number of worker for collecting and deleting. Default: 16")
    parser.add_argument('--once', action="store_true", help="Only runs one iteration. Default: false")
    parser.add_argument('--dryrun', action="store_true", help="Dry run mode. Don't delete a single a record or file (but upload)!")
    args = parser.parse_args()

    config_dict = {}
    
    # Read the configuration file.
    if args.config:
        config = RawConfigParser()
        config.readfp(args.config)
        config_dict.update(dict(config.items('sweeper')))

    # Overwrite the configuration properties.
    for k,v in filter(lambda x: x[1] is not None, [
            ('account_name', args.account), 
            ('account_key', args.key), 
            ('queue_name', args.queue), 
            ('tmpdir', args.tmpdir),
            ('blob_path', args.blobpath),
            ('nr_approx_records_per_file', args.number),
            ('visibility_timeout', args.visibility),
            ('dry_run', args.dryrun),
            ('nr_worker', args.worker),
            ('run_once', args.once),
        ]):
        config_dict[k] = v

    # Check validity.
    for k in ('account_name', 'account_key', 'queue_name', 'blob_path',):
        if not config_dict.get(k):
            print('Missing attribute: {}'.format(k))
            parser.print_help()
            sys.exit(1)

    # Set working directory to the current directory.
    if not config_dict.get('tmpdir'):
        config_dict['tmpdir'] = current_directory

    # Check the directory.
    if not os.path.exists(config_dict['tmpdir']):
        print('Target directory is not exists!')
        parser.print_help()
        sys.exit(1)

    # Check the format of the blob path.
    parsed = urlparse(config_dict['blob_path'])
    if parsed.scheme not in ('wasbs', 'wasb'):
        print('Blob path is not supported! Expected format: `wasb[s]://container/blob-path`')
        sys.exit(1)

    # If approx records number was not defines, then set to the default value.
    if not config_dict.get('nr_approx_records_per_file'):
        config_dict['nr_approx_records_per_file'] = 10000

    # If visibility timeout was not defined then set to the default value.
    if not config_dict.get('visibility_timeout'):
        config_dict['visibility_timeout'] = 10*60

     # If number of workerswas not defined then set to the default value.
    if not config_dict.get('nr_worker'):
        config_dict['nr_worker'] = 16

    return config_dict

def main():
    Dumper(**get_config_dict()).run()