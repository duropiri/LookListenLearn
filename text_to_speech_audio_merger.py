import tkinter as tk
from tkinter import filedialog, simpledialog, messagebox, ttk
from tkinter.ttk import Progressbar
import threading

from PIL import Image, ImageEnhance, ImageFilter
import pytesseract
import fitz  # PyMuPDF
import re
import openai
from pathlib import Path
import shutil
from pydub import AudioSegment
import os
import time
from dotenv import load_dotenv
load_dotenv()
OpenAI_API_KEY = os.environ.get('OPENAI_API_KEY')

# -- Text Extraction from PDF --
# Compile patterns for common header and footer content
# Use specific patterns to match the exact structure of your headers and footers
header_patterns = [
    re.compile(r'^\s*\d+\s*$', re.MULTILINE),  # Page number at the beginning of a line
    
    # Add other header-specific patterns here
]
footer_patterns = [
    # re.compile(r'Licensed to [^\s]+@[^\s]+', re.IGNORECASE),  # Licensing text
    # re.compile(r'Licensed to Emre Sevinc <emre.sevinc@gmail.com>', re.IGNORECASE),
    
    # Add other footer-specific patterns here
]

def is_header_or_footer(block_text, header_patterns, footer_patterns):
    # Check against header and footer patterns
    for pattern in header_patterns + footer_patterns:
        if pattern.search(block_text):
            return True
    return False

def clean_text(block_text):
    # Remove specific unreadable characters
    block_text = block_text.replace('', '')
    block_text = block_text.replace('©', '')

    # Remove image tags using regular expression
    image_pattern = re.compile(r'<image:.*?>')
    block_text = re.sub(image_pattern, '', block_text)

    return block_text

def remove_images_from_pdf(pdf_path):
    doc = fitz.open(pdf_path)
    for page in doc:
        image_list = page.get_images(full=True)
        for img in image_list:
            xref = img[0]
            page.clean_contents()  # Clean the page's contents
            doc._deleteObject(xref)  # Delete the image object

    return doc

def preprocess_image_for_ocr(img):
    # Convert the image to grayscale
    img = img.convert('L')
    # Apply filters to enhance image quality
    img = img.filter(ImageFilter.MedianFilter())
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2)
    return img

def post_process_ocr_text(text):
    # Correct specific OCR misinterpretations for bullet points
    text = re.sub(r'(?m)^\s*¢[\s\t]', '• ', text)  # Replace '¢' with a bullet point
    # Use a regular expression to replace 'e' with a bullet point in specific contexts
    # Replace 'e' at the start of a line followed by whitespace, tab, or newline
    text = re.sub(r'(?m)^\s*e[\s\t]', '• ', text)
    # Correct specific OCR error: "AI" misread as "Al"
    text = re.sub(r'\bAl\b', 'AI', text)

    # # Apply spell checking
    # corrected_text = correct_spelling(text)
    corrected_text = text
    return corrected_text

def extract_text_from_pdf(pdf_path, output_txt_path, use_ocr=True):
    print(f'\nExtracting text from PDF: "{pdf_path}"')
    # Remove images from the PDF
    doc = remove_images_from_pdf(pdf_path)
    extracted_text = ""
    
    for page in doc:
        if use_ocr:
            # Convert the PDF page to a high-resolution image
            pix = page.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72))
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            processed_img = preprocess_image_for_ocr(img)
            # Use Tesseract to do OCR on the processed image
            # Try different PSM values if needed (e.g., 3, 6)
            page_text = pytesseract.image_to_string(processed_img, config='--psm 3')
        else:
            text_blocks = page.get_text("blocks")
            text_blocks.sort(key=lambda block: block[1])  # Sort text blocks by vertical position
            page_text = ""
            for block in text_blocks:
                block_text = block[4].strip()
                if is_header_or_footer(block_text, header_patterns, footer_patterns):
                    continue
                block_text = clean_text(block_text)
                page_text += block_text + "\n\n"

        extracted_text += page_text + "\n\n"

    doc.close()
    
    # Post-process the extracted text
    processed_text = post_process_ocr_text(extracted_text)

    with open(output_txt_path, 'w', encoding='utf-8') as file:
        if use_ocr:
            file.write(processed_text)
        else:
            file.write(extracted_text)
    print(f'\nPDF text written to: "{output_txt_path}"')
    print(f'\nConverting text to speech...\n')

# -- Text to Speech Conversion --
# Initialize a start time variable globally or pass it to the function if you prefer
start_time = time.time()

def update_progress_bar(current, total, bar_length=50):
    # Function to update the progress bar on the console
    fraction_completed = current / total
    arrow = int(fraction_completed * bar_length - 1) * '▮' + '▮'
    padding = (bar_length - len(arrow)) * '▯'
    progress_bar = f'[{arrow}{padding}]'

    # Calculate elapsed time and estimated time remaining
    elapsed_time = time.time() - start_time
    if current == 0:  # Avoid division by zero
        estimated_total_time = 0
    else:
        estimated_total_time = elapsed_time / fraction_completed
    estimated_time_remaining = estimated_total_time - elapsed_time

    # Convert estimated time remaining to a readable format (hours, minutes, seconds)
    hours, rem = divmod(estimated_time_remaining, 3600)
    minutes, seconds = divmod(rem, 60)
    time_remaining_formatted = f'{int(hours):02}:{int(minutes):02}:{int(seconds):02}'

    print(f'Progress: {progress_bar} {current}/{total} chunks processed. Time remaining: {time_remaining_formatted}', end='\r')

def split_text_by_sentence(text, chunk_size=4096):
    # Improved function to ensure chunks are within the character limit and end at sentence boundaries
    sentences = text.split('. ')
    current_chunk = ''
    for sentence in sentences:
        # Check if adding the next sentence would exceed the chunk size
        if len(current_chunk) + len(sentence) + 1 <= chunk_size:
            current_chunk += sentence + '. '
        else:
            # If the current chunk is not empty and the sentence itself is not exceeding the chunk size
            if current_chunk and len(sentence) <= chunk_size:
                yield current_chunk
                current_chunk = sentence + '. '
            else:
                # If the sentence itself is longer than the chunk size, split it further
                # (This part of the logic depends on how you want to handle very long sentences)
                pass
    if current_chunk:
        yield current_chunk

def text_to_speech(api_key, text_path, output_directory):
    # Set your OpenAI API key here
    openai.api_key = api_key

    # Read the content of the text file
    with open(text_path, 'r', encoding='utf-8') as file:
        text_content = file.read()

    # Split the text into chunks
    text_chunks = list(split_text_by_sentence(text_content))
    total_chunks = len(text_chunks)

    # Create the exported folder if it doesn't exist
    exported_folder = Path(output_directory)
    exported_folder.mkdir(exist_ok=True)
    
    # Clear the contents of the folder
    for item in exported_folder.iterdir():
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()

    # Convert each chunk of text to speech and save the audio files
    for i, chunk in enumerate(text_chunks, start=1):
        response = openai.audio.speech.create(
            input=chunk,
            model="tts-1",
            voice="onyx",
        )

        # Path where the speech file for the current chunk will be saved
        speech_file_path = exported_folder / f'speech_chunk_{i}.mp3'
        
        # Stream the response to a file
        response.stream_to_file(str(speech_file_path))
        
        # Update the progress bar
        update_progress_bar(i, total_chunks)
        
    print(f'\nAll speech chunks have been saved. Total chunks: {total_chunks}.\nMerging audio files ...')

# -- Audio Merging --
def extract_number(filename):
    # Extract the numeric part from the filename
    return int(filename.split('_')[-1].split('.')[0])

def merge_audio_files(directory, output_file):
    # Merge audio files
    files = [f for f in os.listdir(directory) if os.path.isfile(os.path.join(directory, f))]
    audio_files = [f for f in files if f.startswith('speech_chunk_') and f.endswith('.mp3')]
    audio_files.sort(key=extract_number)
    if not audio_files:
        print("No audio files found in the directory")
        return
    combined = AudioSegment.from_file(os.path.join(directory, audio_files[0]))
    for file in audio_files[1:]:
        audio = AudioSegment.from_file(os.path.join(directory, file))
        combined += audio
    combined.export(output_file, format='mp3')
    print(f'Completed, output file saved to: {output_file}')

# -- GUI Executable --
# Function to update messages in the text widget
def update_message(message):
    message_box.insert(tk.END, message + "\n")
    message_box.see(tk.END)
    root.update_idletasks()

# Function to ask for API key
def ask_api_key():
    global api_key
    api_key = simpledialog.askstring("Input", "Enter API Key:", parent=root)
    if api_key:
        update_message("API Key entered")

# Function to select PDF
def select_pdf():
    global pdf_path, text_output_path
    pdf_path = filedialog.askopenfilename(filetypes=[("PDF Files", "*.pdf")])
    if pdf_path:
        update_message(f"PDF selected: {pdf_path}")
        # Set the text output path based on the PDF file name
        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        text_output_path = os.path.join(os.path.dirname(pdf_path), base_name + "_text.txt")
        update_message(f"Text output path: {text_output_path}")

# Function to select output folder
def select_output_folder():
    global audio_output_directory, final_audio_output
    audio_output_directory = filedialog.askdirectory()
    if audio_output_directory:
        update_message(f"Output folder selected: {audio_output_directory}")
        # Set the final audio output path
        if pdf_path:
            base_name = os.path.splitext(os.path.basename(pdf_path))[0]
            final_audio_output = os.path.join(audio_output_directory, base_name + "_audio.mp3")
            update_message(f"Final audio output path: {final_audio_output}")

# Start Conversion Process
def start_conversion():
    if not all([api_key, pdf_path, audio_output_directory]):
        messagebox.showwarning("Warning", "Please provide all necessary inputs.")
        return
    threading.Thread(target=conversion_process).start()

# Conversion Process
def conversion_process():
    try:
        update_message("Starting text extraction...")
        progress_bar['value'] = 20
        root.update_idletasks()
        extract_text_from_pdf(pdf_path, text_output_path)

        update_message("Converting text to speech...")
        progress_bar['value'] = 50
        root.update_idletasks()
        text_to_speech(api_key, text_output_path, audio_output_directory)

        update_message("Merging audio files...")
        progress_bar['value'] = 80
        root.update_idletasks()
        merge_audio_files(audio_output_directory, final_audio_output)

        progress_bar['value'] = 100
        root.update_idletasks()
        messagebox.showinfo("Info", "Conversion Completed")
    except Exception as e:
        messagebox.showerror("Error", f"An error occurred: {e}")

# Tkinter UI Setup
root = tk.Tk()
root.title("PDF to Audio Converter")
root.geometry('400x300')  # Set a default size for the window

# Use padding for better spacing
pad_x = 5
pad_y = 5

# Frame for padding and organization
main_frame = ttk.Frame(root, padding="10 10 10 10")
main_frame.grid(column=0, row=0, sticky=(tk.W, tk.E, tk.N, tk.S))

# Configure the main_frame to expand with the window
root.columnconfigure(0, weight=1)
root.rowconfigure(0, weight=1)
main_frame.columnconfigure(1, weight=1)  # Make the column containing the message_box expandable
main_frame.rowconfigure(6, weight=1)     # Make the row containing the message_box expandable

# Place the buttons on the frame
api_key_button = ttk.Button(main_frame, text="Enter API Key", command=ask_api_key)
api_key_button.grid(column=1, row=1, padx=pad_x, pady=pad_y)

pdf_button = ttk.Button(main_frame, text="Select PDF", command=select_pdf)
pdf_button.grid(column=1, row=2, padx=pad_x, pady=pad_y)

folder_button = ttk.Button(main_frame, text="Select Output Folder", command=select_output_folder)
folder_button.grid(column=1, row=3, padx=pad_x, pady=pad_y)

start_button = ttk.Button(main_frame, text="Start Conversion", command=start_conversion)
start_button.grid(column=1, row=4, padx=pad_x, pady=pad_y)

# Add a progress bar
progress_bar = ttk.Progressbar(main_frame, orient=tk.HORIZONTAL, length=100, mode='determinate')
progress_bar.grid(column=1, row=5, padx=pad_x, pady=pad_y, sticky=(tk.W, tk.E))

# Text Widget for displaying messages, configured to be expandable
message_box = tk.Text(main_frame, height=10)
message_box.grid(column=1, row=6, padx=5, pady=5, sticky=(tk.W, tk.E, tk.N, tk.S))

# Make the grid columns and rows adjust to the window size
for child in main_frame.winfo_children(): 
    child.grid_configure(padx=5, pady=5)

# Start the Tkinter event loop
if __name__ == "__main__":
    root.mainloop()

# # -- Main Execution Flow --
# if __name__ == "__main__":
#     api_key = OpenAI_API_KEY
#     # Define file paths and directories
#     pdf_path = "./Textbook Chapters/Artificial Intelligence A Modern Approach (Stuart Russell and Peter Norvig)-Chapter 3.pdf"
#     text_output_path = "./Chapter_3_AI_A_Modern_Approach.txt"
#     audio_output_directory = "./exported"
#     final_audio_output = "./Chapter_3_AI_A_Modern_Approach.mp3"

#     # Extract text from PDF
#     extract_text_from_pdf(pdf_path, text_output_path)

#     # Convert text to speech
#     text_to_speech(api_key, text_output_path, audio_output_directory)

#     # Merge the audio files
#     merge_audio_files(audio_output_directory, final_audio_output)
