#!python3
# Source: https://github.com/sentry-demos/sgm-simulation/blob/master/sgm.py
import math
import random
import tkinter as tk
from tkinter import ttk

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.patches import Patch

ALGORITHM_VERSION = "v0"


def simulate_sgm(daily_spend):
    n = len(daily_spend)

    wallet = 0.0
    accepted_history = []
    wallet_history = []
    DSL = 0.0
    accepted = 0.0
    absolute_limit_days = []  # Track days where absolute limit was used

    for i in range(n):
        # 1) Daily Average Spend from the last 7 days
        recent_7 = sum(accepted_history[-7:])
        recent_6 = sum(accepted_history[-6:])

        # ensure next recent_7 is no more than max($20 higher, 20% higher) than this recent_7 per week
        relative_limit = recent_7 * 1.20 ** (1.0 / 7) - recent_6
        absolute_limit = recent_7 + 20.0 / 7 - recent_6
        daily_spend_limit = max(relative_limit, absolute_limit)

        # Track if absolute limit was used
        if absolute_limit > relative_limit:
            absolute_limit_days.append(i)

        wallet = min(wallet, daily_spend_limit * 2)
        wallet += daily_spend_limit

        accepted = min(wallet, daily_spend[i])

        wallet -= accepted

        # Save
        accepted_history.append(accepted)
        wallet_history.append(wallet)

    return accepted_history, wallet_history, absolute_limit_days


def generate_daily_spend(
    organic_growth,
    baseline_start,
    fluctuation_magnitude,
    fluctuation_offset,
    noise,
    spike_magnitude,
    days,
):

    if days < 52:
        raise ValueError("Days must be at least 52 to fit all spikes")

    spike_offset = round((days - 60) / 2)

    # Generate base daily spend with weekly fluctuation
    daily_spend = [
        max(
            0,
            baseline_start
            * (
                1
                + fluctuation_magnitude
                / 2
                * math.sin((i - spike_offset + fluctuation_offset) * 2 * math.pi / 7)
            )
            * (1 + organic_growth * i / 30)
            * (1 + noise * np.random.normal(0, 1)),
        )
        for i in range(days)
    ]

    # Add spikes with magnitude control
    daily_spend[spike_offset + 10] *= 1.45 * spike_magnitude
    daily_spend[spike_offset + 11] *= 1.55 * spike_magnitude
    daily_spend[spike_offset + 25] *= 2.5 * spike_magnitude
    daily_spend[spike_offset + 26] *= 2.5 * spike_magnitude
    daily_spend[spike_offset + 27] *= 2.5 * spike_magnitude
    daily_spend[spike_offset + 37] *= 2.0 * spike_magnitude
    daily_spend[spike_offset + 40] *= 1.8 * spike_magnitude
    daily_spend[spike_offset + 41] *= 1.8 * spike_magnitude
    daily_spend[spike_offset + 42] *= 1.8 * spike_magnitude
    daily_spend[spike_offset + 43] *= 1.8 * spike_magnitude
    daily_spend[spike_offset + 44] *= 1.8 * spike_magnitude
    daily_spend[spike_offset + 55] *= 2.5 * spike_magnitude

    input_tag = f"{organic_growth:.2f}_{int(baseline_start)}_{fluctuation_magnitude:.2f}_{noise:.2f}_{spike_magnitude:.1f}_{fluctuation_offset:.2f}_{days}"

    return daily_spend, input_tag


def update_plot():
    try:
        days = int(float(days_var.get()))
        if days < 52:
            days = 52
            days_var.set("52")

        # Generate daily spend data
        daily_spend, input_tag = generate_daily_spend(
            organic_growth=float(organic_growth_var.get()),
            baseline_start=float(baseline_start_var.get()),
            fluctuation_magnitude=float(fluctuation_magnitude_var.get()),
            fluctuation_offset=float(fluctuation_offset_var.get()),
            noise=float(noise_var.get()),
            spike_magnitude=float(spike_magnitude_var.get()),
            days=days,
        )

        # Set fixed random seed
        random.seed(42)
        np.random.seed(42)

        # Simulate SGM
        accepted_history, wallet_history, absolute_limit_days = simulate_sgm(
            daily_spend
        )

        # Clear previous plot
        ax.clear()

        # Add background highlighting for absolute limit days
        for day in absolute_limit_days:
            ax.axvspan(day - 0.5, day + 0.5, color="lightblue", alpha=0.3, linewidth=0)

        # Plot data
        daily_line = ax.plot(daily_spend, label="daily spend")[0]
        accepted_line = ax.plot(accepted_history, label="accepted")[0]
        wallet_line = ax.plot(
            wallet_history, label="wallet", linestyle="--", linewidth=1
        )[0]

        # Add legend
        legend_elements = [
            daily_line,
            accepted_line,
            wallet_line,
            Patch(facecolor="lightblue", alpha=0.3, label="$20/wk growth limit"),
        ]
        ax.legend(
            handles=legend_elements, loc="upper left", facecolor="white", framealpha=1.0
        )

        # Set axis limits and labels
        ax.set_xlim(0, days - 1)
        ylim_max = max(
            max(daily_spend) * 1.05,
            max(accepted_history) * 1.05,
            max(wallet_history) * 1.05,
        )
        ax.set_ylim(-0.25 * ylim_max / 100, ylim_max)
        ax.set_xlabel("day")
        ax.set_ylabel("daily spend, $", labelpad=10)

        # Add grid
        ax.grid(axis="y", linestyle="--", alpha=0.25)

        # Update title
        title = f"SGM {ALGORITHM_VERSION} - {input_tag}"
        ax.set_title(title)

        # Redraw canvas
        canvas.draw()
    except ValueError as e:
        # Show error message if input is invalid
        tk.messagebox.showerror(
            "Input Error", "Please enter valid numbers for all fields"
        )


def on_enter(event):
    update_plot()


if __name__ == "__main__":
    # Create main window
    root = tk.Tk()
    root.title("SGM Simulation")

    # Set initial window size
    root.geometry("1200x600")

    # Configure grid weights
    root.grid_rowconfigure(0, weight=1)
    root.grid_columnconfigure(0, weight=1)

    # Create main container
    main_frame = ttk.Frame(root)
    main_frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

    # Configure main frame grid
    main_frame.grid_rowconfigure(0, weight=1)
    main_frame.grid_columnconfigure(0, weight=1)

    # Create plot area
    plot_frame = ttk.Frame(main_frame)
    plot_frame.grid(row=0, column=0, sticky="nsew")

    # Create figure and axis with higher DPI
    fig, ax = plt.subplots(figsize=(8, 4), dpi=50)
    canvas = FigureCanvasTkAgg(fig, master=plot_frame)
    canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    # Create controls area
    controls_frame = ttk.Frame(main_frame)
    controls_frame.grid(row=1, column=0, sticky="ew", pady=(0, 10))

    # Create input fields container with grid
    inputs_container = ttk.Frame(controls_frame)
    inputs_container.pack(fill=tk.X, pady=10)

    # Configure grid for inputs - 2 rows
    for i in range(4):  # 4 columns per row
        inputs_container.grid_columnconfigure(i, weight=1)
    inputs_container.grid_rowconfigure(0, weight=1)
    inputs_container.grid_rowconfigure(1, weight=1)

    # Create variables and entry fields
    organic_growth_var = tk.StringVar(value="0.05")
    baseline_start_var = tk.StringVar(value="30")
    fluctuation_magnitude_var = tk.StringVar(value="0.4")
    fluctuation_offset_var = tk.StringVar(value="5.5")
    noise_var = tk.StringVar(value="0.03")
    days_var = tk.StringVar(value="70")
    spike_magnitude_var = tk.StringVar(value="1.0")

    # Create entry fields with labels
    def create_entry(parent, label, var, row, col):
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=col, sticky="ew", padx=5, pady=2)

        # Create label with fixed width
        label_widget = ttk.Label(frame, text=label, width=15, anchor="e")
        label_widget.pack(side=tk.LEFT, padx=(0, 5))

        # Create entry with fixed width
        entry = ttk.Entry(frame, textvariable=var, width=8)
        entry.pack(side=tk.LEFT)
        entry.bind("<Return>", on_enter)
        return entry

    # Add input fields in two rows
    # First row
    create_entry(inputs_container, "Organic Growth:", organic_growth_var, 0, 0)
    create_entry(inputs_container, "Baseline Start:", baseline_start_var, 0, 1)
    create_entry(
        inputs_container, "Fluctuation Magnitude:", fluctuation_magnitude_var, 0, 2
    )
    create_entry(inputs_container, "Fluctuation Offset:", fluctuation_offset_var, 0, 3)

    # Second row
    create_entry(inputs_container, "Noise:", noise_var, 1, 0)
    create_entry(inputs_container, "Spike Magnitude:", spike_magnitude_var, 1, 1)
    create_entry(inputs_container, "Days:", days_var, 1, 2)

    # Create Simulate button at the end of second row
    simulate_button = ttk.Button(
        inputs_container, text="Run Simulation (Enter)", command=update_plot, width=19
    )
    simulate_button.grid(row=1, column=3, padx=25, pady=2, sticky="w")

    # Bind Enter key to the root window
    root.bind("<Return>", on_enter)

    # Initial plot
    update_plot()

    # Start the GUI
    root.mainloop()
