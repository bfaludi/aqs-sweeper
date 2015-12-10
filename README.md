# Azure Queue Storage sweeper

Extendable Azure Queue Storage dumper that copies your data into Azure Blob Storage. It works in **Python 2.7** and **PyPy2** and provides a **command line interface**. This package was tested on OSX and Linux.

This package does the following:

- Dumps approx. `n` messages parallel from Azure Queue Storage.
- Decodes the messages and puts to a temporary JSON file.
- Converts the file and creates a new one (need to extend).
- Uploads the final file into Azure Blob Storage.
- Deletes the messages from Azure Queue Storage.
- Repeats it until it can dump any messages.


## Installation

You can install easily with `pip`.

```bash
$ pip install aqs-sweeper
```

**Note**: You won't be able to install it with `easy_install` because of the incompatibility between `setuptools` and `azure` libraries.

## Usage

Using the original script is easy. You'll get an `aqs-sweep` script.

```bash
aqs-sweep [-h] [-c CONFIG] [-a ACCOUNT] [-k KEY] [-q QUEUE]
          [-p BLOBPATH] [-d TMPDIR] [-n NUMBER] [-v VISIBILITY]
          [-w WORKER] [--once] [--dryrun]
```

I recommend to **use at least 10,000 records per file** and to **use 16 or 32 workers**. This script uses only one CPU at the time, to maximalize the performance you can use the `parallels` package. e.g.: If you've 4 CPUs then you'd run this

```bash
seq 4 | parallel -n0 -j4 "aqs-sweep ..."
```

If you're mixing parameters and a configuration file, parameters will override the configuration file's values.

**Parameters**:

- `-c` `--config`: Configuration file (optional).
- `-a` `--account`: Azure Storage's account name.
- `-k` `--key`: Azure Storage's account key.
- `-q` `--queue`: Queue name of the Azure Queue Storage.
- `-p` `--blobpath`: Azure Blob Storage's path to store the final files. Example: `wasbs://container/blob/path/`
- `-d` `--tmpdir`: Temporary files will be created here.
- `-n` `--number`: One file will contain approx. this amount of line.
- `-v` `--visibility`: Length of the invisible state of a message in move in seconds.
- `-w` `--worker`: Dumping and deleting are asynchronous. Number of workers you want to use.
- `--once`: Stops after the first iteration.
- `--dryrun`: Dry-run mode. Doesn't delete the messages and the messages will be available after the read. It won't remove the temporary file's content.

For testing purposes I'd recommend to use the `-o` and `--dryrun` options.

### Configuration

You can create a configuration file to set up the library and to avoid unecessary parameters. Save this as `config.ini`.

```ini
[sweeper]
account_name=account-name
account_key=account-key
queue_name=name-of-the-queue
nr_approx_records_per_file=10000
visibility_timeout=600
blob_path=wasbs://container/blob/path/{timestamp}/
nr_worker=32
tmpdir=/tmp/
```

Parameter names are a bit different but it's understandable.


## Extend with your business logic

You can extend the dumper easily.

#### Decode JSON messages from the Queue.

```python
import json
from aqs_sweeper import Dumper, get_config_dict

class MyDumper(Dumper):
    def decode_message_text(self, message_text):
        return json.loads(message_text)

if __name__ == '__main__':
    MyDumper(**get_config_dict()).run()
```

#### Convert your files if necessary.

```python
import io
import json
from aqs_sweeper import Dumper, get_config_dict

class MyDumper(Dumper):
    def create_output_file(self, json_filename):
        csv_filename = self.create_temporary_file('.csv')
        with io.open(csv_filename, 'w', encoding='utf-8') as out, \
             io.open(json_filename, 'r', encofing='utf-8') as inp:
            for line in inp:
                data = json.loads(line)
                # ...

        return csv_filename

if __name__ == '__main__':
    MyDumper(**get_config_dict()).run()
```

## License

Copyright © 2015 Bence Faludi.

Distributed under the MIT License.
