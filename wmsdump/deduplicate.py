import json
import logging

from pathlib import Path

import click

from wmsdump.logging import setup_logging

logger = logging.getLogger(__name__)


@click.command()
@click.argument('input-file',
                type=click.Path(exists=True), required=True)
@click.argument('output-file',
                type=click.Path(), required=False)
@click.option('--log-level', '-l',
              type=click.Choice(['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
                                case_sensitive=False),
              default='INFO', show_default=True,
              help='set logging level')
@click.option('--use-offset/--use-ram',
              default=True, show_default=True,
              help='use file offset for collision checks (default) or keep features in RAM')
def main(input_file, output_file, log_level, use_offset):
    """Deduplicate features in a GeoJSONL file based on geometry and properties."""
    setup_logging(log_level)
    
    if output_file is None:
        input_path = Path(input_file)
        output_file = str(input_path.parent / f'deduped_{input_path.name}')
        logger.info(f'output file not specified.. writing to {output_file}')
    
    seen = {}
    total_count = 0
    unique_count = 0
    
    logger.info(f'deduplicating {input_file} (mode: {"offset" if use_offset else "RAM"})')
    
    try:
        with open(input_file, 'r') as infile, open(output_file, 'w') as outfile:
            while True:
                offset = infile.tell()
                line = infile.readline()
                if not line:
                    break
                total_count += 1
                line = line.strip()
                if not line:
                    continue
                
                try:
                    feat = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(f'skipping invalid JSON at line {total_count}')
                    continue

                feat_str = json.dumps(feat, sort_keys=True)
                feat_hash = hash(feat_str)
                
                # Check for hash collision
                if feat_hash in seen:
                    if use_offset:
                        # Read from file offset to compare
                        current_pos = infile.tell()

                        infile.seek(seen[feat_hash])
                        stored_line = infile.readline().strip()
                        stored_feat = json.loads(stored_line)
                        stored_feat_str = json.dumps(stored_feat, sort_keys=True)

                        infile.seek(current_pos)
                    else:
                        stored_feat_str = seen[feat_hash]
                        
                    if stored_feat_str == feat_str:
                        continue

                    # Hash collision, this is actually unique
                    logger.debug(f'hash collision detected at line {total_count}')
                
                # Store hash with either offset or full content
                seen[feat_hash] = offset if use_offset else feat_str
                outfile.write(line)
                outfile.write('\n')
                unique_count += 1
                    
        
        logger.info(f'processed {total_count} features, wrote {unique_count} unique features')
        logger.info(f'removed {total_count - unique_count} duplicates')
        logger.info(f'output written to {output_file}')
        
    except Exception as e:
        logger.exception(f'error during deduplication: {e}')
        raise


if __name__ == '__main__':
    main()
