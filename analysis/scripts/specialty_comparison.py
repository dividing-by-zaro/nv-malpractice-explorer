"""
Compare Nevada malpractice cases by specialty against national benchmark data.
Shows both as percentages of total claims for comparison.
"""

import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

from load_data import load_complaints

# Load Nevada data
complaints = load_complaints()
nevada_counts = complaints['llm_specialty'].value_counts()

# Load national benchmark data
datasets_dir = Path(__file__).parent.parent / "datasets"
national = pd.read_csv(datasets_dir / "malpractice_claims_1992_2014.csv")

# Map Nevada specialties to national dataset specialties
specialty_mapping = {
    'Internal Medicine': 'Internal medicine',
    'Family Medicine': 'Family Medicine',
    'Emergency Medicine': 'Emergency medicine',
    'Obstetrics and Gynecology': 'Obstetrics and gynecology',
    'Orthopedics': 'Orthopedics',
    'General Surgery': 'General surgery',
    'Neurosurgery': 'Neurosurgery',
    'Anesthesiology': 'Anesthesiology',
    'Psychiatry': 'Psychiatry',
    'Cardiology': 'Cardiology',
    'Radiology': 'Radiology',
    'Pediatrics': 'Pediatrics',
    'Neurology': 'Neurology',
    'Urology': 'Urology',
    'Dermatology': 'Dermatology',
    'Ophthalmology': 'Ophthalmology',
    'Gastroenterology': 'Gastroenterology',
    'Pulmonology': 'Pulmonology',
    'Pathology': 'Pathology',
    'Plastic Surgery': 'Plastic surgery',
    'Otolaryngology': 'Otolaryngology',
    'Thoracic Surgery': 'Thoracic surgery',
}

# Get total national claims (excluding "All specialties" row)
national_specialties = national[national['Specialty'] != 'All specialties']
total_national_claims = national_specialties['Total Paid Claims No.'].sum()

# Build comparison dataframe
comparison_data = []
for nevada_spec, national_spec in specialty_mapping.items():
    nevada_count = nevada_counts.get(nevada_spec, 0)
    national_row = national[national['Specialty'] == national_spec]
    if not national_row.empty:
        national_claims = national_row['Total Paid Claims No.'].values[0]
        comparison_data.append({
            'specialty': nevada_spec,
            'nevada_count': nevada_count,
            'national_claims': national_claims
        })

df = pd.DataFrame(comparison_data)

# Calculate percentages of total claims
total_nevada = df['nevada_count'].sum()
df['nevada_pct'] = (df['nevada_count'] / total_nevada) * 100
df['national_pct'] = (df['national_claims'] / total_national_claims) * 100

# Sort by Nevada percentage descending
df = df.sort_values('nevada_pct', ascending=True)

# Create grouped bar chart
fig, ax = plt.subplots(figsize=(12, 10))

y_pos = range(len(df))
bar_height = 0.35

# Plot grouped bars
bars1 = ax.barh([y - bar_height/2 for y in y_pos], df['nevada_pct'],
                bar_height, label=f'Nevada (n={total_nevada:,})', color='#2563eb', alpha=0.85)
bars2 = ax.barh([y + bar_height/2 for y in y_pos], df['national_pct'],
                bar_height, label=f'National (n={total_national_claims:,})', color='#64748b', alpha=0.85)

# Customize
ax.set_yticks(y_pos)
ax.set_yticklabels(df['specialty'], fontsize=10)
ax.set_xlabel('Proportion of All Claims (%)', fontsize=11)
ax.set_title('Distribution of Malpractice Claims by Specialty\nNevada (2008-2025) vs National (1992-2014)',
             fontsize=13, fontweight='bold', pad=20)
ax.legend(loc='lower right', fontsize=10)

# Add gridlines
ax.xaxis.grid(True, linestyle='--', alpha=0.3)
ax.set_axisbelow(True)

plt.tight_layout()

# Save to output directory
output_dir = Path(__file__).parent.parent / "output"
output_dir.mkdir(exist_ok=True)
output_path = output_dir / "specialty_comparison.png"
plt.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='white')
print(f"Chart saved to: {output_path}")

# Summary stats
print("\n" + "="*60)
print("Claims Distribution by Specialty")
print("="*60)
print(f"\nNevada total: {total_nevada} claims")
print(f"National total: {total_national_claims:,} claims")

print(f"\nLargest differences (Nevada % - National %):")
df['diff'] = df['nevada_pct'] - df['national_pct']
for _, row in df.nlargest(5, 'diff').iterrows():
    print(f"  {row['specialty']}: {row['diff']:+.1f}pp (NV: {row['nevada_pct']:.1f}%, Nat: {row['national_pct']:.1f}%)")

print(f"\nSmallest differences:")
for _, row in df.nsmallest(5, 'diff').iterrows():
    print(f"  {row['specialty']}: {row['diff']:+.1f}pp (NV: {row['nevada_pct']:.1f}%, Nat: {row['national_pct']:.1f}%)")

# plt.show()  # Uncomment for interactive viewing
