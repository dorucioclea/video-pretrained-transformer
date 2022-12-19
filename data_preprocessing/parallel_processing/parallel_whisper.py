import os 
import json
import cv2
import numpy as np
import clip
import torch
from PIL import Image
import pathlib
from pathlib import Path
import glob
import argparse
import jsonlines
import json
import time
import deeplake as dl
import tqdm
import sys
import ray
import random
import more_itertools




BATCH_NAME          = 'parallel_15'
WHISPER_RESULTS_DATASET_PATH        = f'/mnt/storage_ssd/v1_whisper_results_{BATCH_NAME}'
INPUT_DATASET_PATH        = f'/mnt/storage_ssd/v0_for_whisper_{BATCH_NAME}'
BASE_DIR            = '/mnt/storage_hdd/thesis/yt_1b_dataset/yt_1b_train/'
LOCAL_VIDEO_DIR         = f'/tmp/{BATCH_NAME}' # used for wavs

NUM_GPUS = 1
NUM_THREADS = 2 # first one always dies for some reason.
NUM_CPU_CORES = 5


def write_error(file):
  failed_file_json_object = json.dumps(str(file))
  empty_filepath = LOCAL_VIDEO_DIR + "_whisper_empty.jsonl"
  if not os.path.exists(empty_filepath):
      pathlib.Path(empty_filepath).touch()
  with jsonlines.open(empty_filepath, mode='a') as writer:
      writer.write({"video_filepath": failed_file_json_object}) 

@ray.remote(num_cpus=2, num_gpus=0.5) # .70 and 1/30 equals 65% DRAM usage right immediately. Can't really go any higher.
def parallel_caption_extraction(file_batch, itr):
  # sys.path.append("/u/kastanday/parallel_pdg/video-pretrained-transformer/data_preprocessing/whisper_audio")
  sys.path.append("/home/kastan/thesis/video-pretrained-transformer/data_preprocessing/whisper_audio")
  import CaptionPreprocessing as CaptionPreprocessing
  start = time.monotonic()
  process = CaptionPreprocessing.CaptionPreprocessing()
  for index, file in enumerate(file_batch):
    try:
      # MAIN: run whisper
      process.load_mp4_to_wav_with_outpath(file, out_dir = LOCAL_VIDEO_DIR + "_wav")
      whisper_one_video_results = process.get_segments_thresholded()
      # If there are no captions/if it is non-english
      if not whisper_one_video_results:
        print("File is empty!")
        write_error(file)
      else:
        dataset_future = add_to_dataset.remote(whisper_one_video_results)
        ray.get(dataset_future)
      print("✅ Success: ", file)
    except Exception as e:
      print(file)
      # write failed files to jsonlines
      print(f"Error during whisper: {e}")
      failed_file_json_object = json.dumps(str(file))
      error_filepath = LOCAL_VIDEO_DIR + "_whisper_errors.jsonl"
      if not os.path.exists(error_filepath):
          pathlib.Path(error_filepath).touch()
      with jsonlines.open(error_filepath, mode='a') as writer:
          writer.write({"video_filepath": failed_file_json_object, "error": str(e)}) 


    # one file done        
    # print(f"⏰ Time to Whisper the file: {(time.monotonic() - start)/60:.2f} minutes\nVideo filesize: {os.path.getsize(file)/1e6:.2f} MB\n")
    start = time.monotonic()

@ray.remote(num_cpus=1)
def add_to_dataset(whisper_one_video_results):
  print("Entering add_to_dataset")
  ds = dl.load(WHISPER_RESULTS_DATASET_PATH)
  with ds:
    for segment in whisper_one_video_results:
      metadata = {
                    "start": str(segment["start"]),
                    "end": str(segment["end"]),
                    "segment_word_list": segment["segment_word_list"],
                    "segment_index": str(segment["segment_index"]),
                    "total_segments": str(segment["total_segments"])
                  }
      ds.caption.append(segment['caption'])
      ds.video_filename.append(segment["video_filename_name"])
      ds.video_filepath.append(segment["video_filepath"])
      ds.segment_metadata.append(metadata)
  ds.flush()
  print(ds.summary())

def main():
  """ MAIN """

  # ray.shutdown()
  ray.init(num_gpus=NUM_GPUS, num_cpus=NUM_CPU_CORES, include_dashboard = False, ignore_reinit_error=True) # , num_gpus = 1
  print_cluster_stats()
  start = time.time()
  ds = dl.load(INPUT_DATASET_PATH)
  ds.summary()
  files = ds.video_filepath.data()['value']

  # filter out bad files (.vtt and .wav, and .json) Anything other than webm and mp4?

  files = [ str(file) for file in files if not str(file).endswith( ('.txt','.vtt', 'json') ) ]
  print("After filtering -- Number of files:", len(files))
  if os.path.exists(WHISPER_RESULTS_DATASET_PATH):
    # Filter files that we need to process
    print(f'Number of files before filtering completed files {len(files)}')
    ds_completed = dl.load(WHISPER_RESULTS_DATASET_PATH)
    try:
      completed_videos = set()
      for index, data_row in enumerate(ds_completed):
        # try:
        completed_videos.add(data_row.video_filepath.data()['value'])
        # except Exception as e:
        #   print("Datalake unable to load index", index, "error is", e)
      print("num vidoes processed", len(list(completed_videos)))
      files = set(files) - completed_videos
      files = list(files)
      print(f'Number of files after filtering completed files {len(files)}')
    except Exception as e:
      print("Error", e)
      print("There is an empty database already created")

  else:
    # Create completed files database
    ds = dl.empty(WHISPER_RESULTS_DATASET_PATH, overwrite=True)
    with ds:
      ds.create_tensor('caption', htype='text', dtype=str, chunk_compression="lz4")
      ds.create_tensor('video_filename', htype='text', dtype=str, sample_compression=None)
      ds.create_tensor('video_filepath', htype='text', dtype=str, sample_compression=None)
      ds.create_tensor('segment_metadata', htype='json', sample_compression="lz4")

  if NUM_THREADS == 1:
    batches = [files]
  else:
    batches = list(more_itertools.divide(NUM_THREADS, files))
  # print batch stats
  print("Num batches: ", len(batches))
  print(len(batches), " should equal num threads: ", NUM_THREADS)
  assert len(batches) == (NUM_THREADS)

  if not os.path.isdir(LOCAL_VIDEO_DIR + "_wav"):
    os.mkdir(LOCAL_VIDEO_DIR + "_wav")
      
  # all_results = ray.get([parallel_caption_extraction.remote(batch, itr) for itr, batch in enumerate(batches)])
  print("Starting parallel batches")
  all_result_futures = [parallel_caption_extraction.remote(batch, itr) for itr, batch in enumerate(batches)]
  
  all_done = ray.get(all_result_futures)

  print("Len of all threads: ", len(all_done))
  print("👉 Completed, finished main().")

def print_cluster_stats():
    print("Querying size of Ray cluster...\n")

    # print at start of staging
    print(f'''This cluster consists of
        {len(ray.nodes())} nodes in total
        {ray.cluster_resources()['CPU']} CPU cores in total
        {ray.cluster_resources()['memory']/1e9:.2f} GB CPU memory in total''')
    if ('GPU' in str(ray.cluster_resources())):
        print(f"        {ray.cluster_resources()['GPU']} GRAPHICCSSZZ cards in total")

if __name__ == '__main__':
    main()