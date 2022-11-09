import CaptionPreprocessing
import pathlib
import os
import time
import json
import ray
import subprocess
import shlex


INPUT_DIR_TO_TRANSCRIBE = '/mnt/storage_ssd/yt1b_train_slice'

@ray.remote
def parallel_caption_extraction(file_batch, itr):
    process = CaptionPreprocessing.CaptionPreprocessing()

    # Write these to disk
    failed_files = []
    start = time.time()
    for index, file in enumerate(file_batch):
        try:
            process.load_mp4_to_wav_with_outpath(file, out_dir = INPUT_DIR_TO_TRANSCRIBE + "_wav")
            process.get_segments_thresholded()
            process.output_json(INPUT_DIR_TO_TRANSCRIBE + "_json")
        except Exception as e:
            print(f"Error during whisper: {e}")
            # We could do this if we don't want to count files that failed
            failed_files.append(file)

        # one file done        
        print(f"⏰ Time to Whisper the file: {(time.time() - start)/60:.2f} minutes\n🔮 Estimated total runtime: { ((time.time() - start)/60) / ((index+1)/len(file_batch)) :.2f} minutes.\nVideo filesize: {os.path.getsize(file)/1e6:.2f} MB\n")
        start = time.time()
            
    with open(f'/mnt/storage_ssd/yt_train_1b_slice_failed_files_{itr}.json', 'w') as f:
        json.dump(failed_files, f, indent=2)
        
def make_batch(items, batch_size):
    """
    Simple helper.
    Create batches of a given size from a list of items.
    """
    return [items[i:i + batch_size] for i in range(0, len(items), batch_size)]

def main():
    """ MAIN """
    NUM_THREADS = 9
    
    ray.shutdown()
    ray.init(num_cpus = NUM_THREADS)
    
    
    # glob files in INPUT_DIR_TO_TRANSCRIBE
    dir_path = pathlib.Path(INPUT_DIR_TO_TRANSCRIBE)
    files = list(dir_path.iterdir())
    print("Number of files:", len(files))
    
    # batches = make_batch(files, NUM_THREADS)
    batches = [files]
    
    if not os.path.isdir(INPUT_DIR_TO_TRANSCRIBE + "_wav"):
        os.mkdir(INPUT_DIR_TO_TRANSCRIBE + "_wav")
    if not os.path.isdir(INPUT_DIR_TO_TRANSCRIBE + "_json"):
        os.mkdir(INPUT_DIR_TO_TRANSCRIBE + "_json")

    all_results = ray.get([parallel_caption_extraction.remote(batch, itr) for itr, batch in enumerate(batches)])

    print(len(all_results))
    print(all_results)
    print("👉 Completed, finished main().")

if __name__ == '__main__':
  main()