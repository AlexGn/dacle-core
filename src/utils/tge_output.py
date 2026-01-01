#!/usr/bin/env python3
"""
Terminal Output Formatting for TGE Analysis

Provides consistent colored terminal output for TGE analysis scripts.
Extracted from run_tge_analysis.py (Phase 3: Code Cleanup)

Usage:
    from src.utils.tge_output import print_phase, print_success, print_error

    print_phase(1, "Data Loading")
    print_success("Data loaded successfully")
    print_error("Failed to load data")

Created: 2025-11-19 (Phase 3: Large File Refactoring)
"""


class Colors:
    """ANSI color codes for terminal output"""
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"


def print_phase(phase_num: int, title: str):
    """Print phase header with consistent formatting"""
    print(f"\n{Colors.BOLD}{Colors.CYAN}{'='*70}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.CYAN}Phase {phase_num}: {title}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.CYAN}{'='*70}{Colors.ENDC}\n")


def print_success(message: str):
    """Print success message"""
    print(f"{Colors.GREEN}✓ {message}{Colors.ENDC}")


def print_error(message: str):
    """Print error message"""
    print(f"{Colors.RED}✗ {message}{Colors.ENDC}")


def print_warning(message: str):
    """Print warning message"""
    print(f"{Colors.YELLOW}⚠ {message}{Colors.ENDC}")


def print_info(message: str):
    """Print info message"""
    print(f"{Colors.BLUE}ℹ {message}{Colors.ENDC}")


def print_header(title: str, subtitle: str = ""):
    """Print main header for script"""
    print(f"\n{Colors.BOLD}{Colors.HEADER}{'='*70}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.HEADER}{title}{Colors.ENDC}")
    if subtitle:
        print(f"{Colors.BOLD}{Colors.HEADER}{subtitle}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.HEADER}{'='*70}{Colors.ENDC}")


def print_section(title: str):
    """Print section divider"""
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*70}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.BLUE}{title}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'='*70}{Colors.ENDC}\n")


def print_completion(title: str):
    """Print completion message"""
    print(f"\n{Colors.BOLD}{Colors.GREEN}{'='*70}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.GREEN}✓ {title}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.GREEN}{'='*70}{Colors.ENDC}")
