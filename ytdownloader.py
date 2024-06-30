import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pytube import YouTube
import threading
import os
import webbrowser

class YouTubeDownloaderApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("YouTube Downloader")
        self.geometry("700x500")

        self.create_widgets()
        self.downloads = {}  # To keep track of download paths

    def create_widgets(self):
        # Tabs
        self.tabs = ttk.Notebook(self)
        self.download_tab = ttk.Frame(self.tabs)
        self.activity_tab = ttk.Frame(self.tabs)
        self.convert_tab = ttk.Frame(self.tabs)
        self.tabs.add(self.download_tab, text='Download')
        self.tabs.add(self.activity_tab, text='Activity')
        self.tabs.add(self.convert_tab, text='Convert')
        self.tabs.pack(expand=1, fill='both')

        # Download Tab
        self.url_label = ttk.Label(self.download_tab, text="Enter the URL of the video you want to download")
        self.url_label.pack(pady=5)
        self.url_entry = ttk.Entry(self.download_tab, width=50)
        self.url_entry.pack(pady=5)
        self.download_button = ttk.Button(self.download_tab, text="Download", command=self.start_download)
        self.download_button.pack(pady=5)
        
        self.quality_label = ttk.Label(self.download_tab, text="Download quality")
        self.quality_label.pack(pady=5)
        self.quality_combobox = ttk.Combobox(self.download_tab, values=[
            "Best Available",
            "4320p 8K",
            "2160p 4K",
            "1440p 2K",
            "1080p Full HD",
            "720p HD",
            "480p Standard",
            "360p Medium (MP4)",
            "240p Low",
            "144p Very Low"
        ])
        self.quality_combobox.current(0)
        self.quality_combobox.pack(pady=5)

        self.subtitles_var = tk.BooleanVar()
        self.subtitles_check = ttk.Checkbutton(self.download_tab, text="Automatically download subtitles", variable=self.subtitles_var)
        self.subtitles_check.pack(pady=5)

        self.path_label = ttk.Label(self.download_tab, text="Save to")
        self.path_label.pack(pady=5)
        self.path_entry = ttk.Entry(self.download_tab, width=50)
        self.path_entry.pack(pady=5)
        self.browse_button = ttk.Button(self.download_tab, text="Browse", command=self.browse_path)
        self.browse_button.pack(pady=5)

        # Activity Tab
        self.activity_tree = ttk.Treeview(self.activity_tab, columns=("Video", "Size", "Progress", "Speed", "Status", "ETA"), show='headings')
        self.activity_tree.heading("Video", text="Video")
        self.activity_tree.heading("Size", text="Size")
        self.activity_tree.heading("Progress", text="Progress")
        self.activity_tree.heading("Speed", text="Speed")
        self.activity_tree.heading("Status", text="Status")
        self.activity_tree.heading("ETA", text="ETA")
        self.activity_tree.pack(expand=1, fill='both')
        self.activity_tree.bind("<Button-3>", self.show_context_menu)

        self.context_menu = tk.Menu(self, tearoff=0)
        self.context_menu.add_command(label="Play", command=self.play_video)
        self.context_menu.add_command(label="Delete from activity tab", command=self.delete_from_activity_tab)
        self.context_menu.add_command(label="Delete file from your computer", command=self.delete_file)
        self.context_menu.add_command(label="Stop", command=self.stop_download)
        self.context_menu.add_command(label="Pause", command=self.pause_download)
        self.context_menu.add_command(label="Rename", command=self.rename_file)
        self.context_menu.add_command(label="Copy video URL", command=self.copy_video_url)
        self.context_menu.add_command(label="Open containing folder", command=self.open_containing_folder)
        self.context_menu.add_command(label="Convert to Ipad Video (MPEG-4 MP4)", command=self.convert_video)

        # Convert Tab
        self.file_label = ttk.Label(self.convert_tab, text="Select the video file")
        self.file_label.pack(pady=5)
        self.file_button = ttk.Button(self.convert_tab, text="Browse", command=self.browse_file)
        self.file_button.pack(pady=5)
        
        self.convert_quality_label = ttk.Label(self.convert_tab, text="Conversion quality")
        self.convert_quality_label.pack(pady=5)
        self.convert_quality_combobox = ttk.Combobox(self.convert_tab, values=["High", "Medium", "Low"])
        self.convert_quality_combobox.current(0)
        self.convert_quality_combobox.pack(pady=5)

        self.delete_original_var = tk.BooleanVar()
        self.delete_original_check = ttk.Checkbutton(self.convert_tab, text="Delete original file after conversion", variable=self.delete_original_var)
        self.delete_original_check.pack(pady=5)
        
        self.convert_button = ttk.Button(self.convert_tab, text="Convert", command=self.convert_video)
        self.convert_button.pack(pady=5)

    def start_download(self):
        url = self.url_entry.get()
        path = self.path_entry.get()
        quality = self.quality_combobox.get()
        if not url or not path:
            messagebox.showerror("Error", "Please provide both URL and save path.")
            return

        thread = threading.Thread(target=self.download_video, args=(url, path, quality))
        thread.start()

    def progress_function(self, stream, chunk, bytes_remaining):
        total_size = stream.filesize
        bytes_downloaded = total_size - bytes_remaining 
        percentage_of_completion = bytes_downloaded / total_size * 100
        progress = f'{int(percentage_of_completion)}%'
        self.activity_tree.item(self.download_item, values=(self.download_video_title, f'{total_size / 1024 / 1024:.2f} MB', progress, 'N/A', 'Downloading', 'N/A'))
        self.update_idletasks()

    def complete_function(self, stream, file_path):
        self.activity_tree.item(self.download_item, values=(self.download_video_title, f'{stream.filesize / 1024 / 1024:.2f} MB', '100%', 'N/A', 'Completed', 'N/A'))
        messagebox.showinfo("Success", "Download completed successfully.")
        self.downloads[self.download_item] = file_path

    def download_video(self, url, path, quality):
        yt = YouTube(url, on_progress_callback=self.progress_function, on_complete_callback=self.complete_function)
        self.download_video_title = yt.title

        video_stream = None
        if quality == "Best Available":
            video_stream = yt.streams.filter(file_extension='mp4').get_highest_resolution()
        else:
            resolution = quality.split()[0]
            video_stream = yt.streams.filter(res=resolution, file_extension='mp4').first()

        if not video_stream:
            video_stream = yt.streams.filter(file_extension='mp4').get_highest_resolution()

        self.download_item = self.activity_tree.insert("", "end", values=(self.download_video_title, f'{video_stream.filesize / 1024 / 1024:.2f} MB', '0%', 'N/A', 'Pending', 'N/A'))
        video_stream.download(path)

    def browse_path(self):
        path = filedialog.askdirectory()
        if path:
            self.path_entry.delete(0, tk.END)
            self.path_entry.insert(0, path)

    def browse_file(self):
        file = filedialog.askopenfilename(filetypes=[("Video Files", "*.mp4 *.avi *.mkv")])
        if file:
            self.file_label.config(text=file)

    def show_context_menu(self, event):
        selected_item = self.activity_tree.identify_row(event.y)
        if selected_item:
            self.activity_tree.selection_set(selected_item)
            self.context_menu.post(event.x_root, event.y_root)

    def play_video(self):
        selected_item = self.activity_tree.selection()[0]
        file_path = self.downloads.get(selected_item)
        if file_path:
            os.startfile(file_path)

    def delete_from_activity_tab(self):
        selected_item = self.activity_tree.selection()[0]
        self.activity_tree.delete(selected_item)

    def delete_file(self):
        selected_item = self.activity_tree.selection()[0]
        file_path = self.downloads.get(selected_item)
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
            self.delete_from_activity_tab()

    def stop_download(self):
        messagebox.showinfo("Stop", "This feature is not implemented yet.")

    def pause_download(self):
        messagebox.showinfo("Pause", "This feature is not implemented yet.")

    def rename_file(self):
        selected_item = self.activity_tree.selection()[0]
        file_path = self.downloads.get(selected_item)
        if file_path:
            new_name = filedialog.asksaveasfilename(initialfile=os.path.basename(file_path), defaultextension=".mp4", filetypes=[("MP4 files", "*.mp4")])
            if new_name:
                os.rename(file_path, new_name)
                self.downloads[selected_item] = new_name

    def copy_video_url(self):
        selected_item = self.activity_tree.selection()[0]
        url = self.activity_tree.item(selected_item)["values"][0]
        self.clipboard_clear()
        self.clipboard_append(url)
        messagebox.showinfo("URL Copied", "The video URL has been copied to clipboard.")

    def open_containing_folder(self):
        selected_item = self.activity_tree.selection()[0]
        file_path = self.downloads.get(selected_item)
        if file_path:
            folder = os.path.dirname(file_path)
            webbrowser.open(f'file://{folder}')

    def convert_video(self):
        selected_item = self.activity_tree.selection()[0]
        file_path = self.downloads.get(selected_item)
        if file_path:
            messagebox.showinfo("Convert", f"Conversion for {file_path} is not implemented yet.")

if __name__ == "__main__":
    app = YouTubeDownloaderApp()
    app.mainloop()
