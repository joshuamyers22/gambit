//
//  csv_reader.hpp
//  py_c_test
//
//  Created by Sal Abbasi on 9/12/22.
//

#ifndef csv_reader_hpp
#define csv_reader_hpp

#include <vector>
#include <string>

struct CsvLimits {
    size_t max_input_bytes = 1024ULL * 1024 * 1024;
    size_t max_output_bytes = 256ULL * 1024 * 1024;
    size_t max_columns = 4096;
};

// Own packed native-endian values, including already-truncated/padded strings.
// No type-erased owning pointers or dtype-dependent destruction.
struct CsvColumn {
    std::string dtype;
    size_t itemsize;
    std::vector<unsigned char> values;
    CsvColumn(const std::string& type, size_t width): dtype(type), itemsize(width) {}
};

size_t csv_itemsize(const std::string& dtype);

bool read_csv(const std::string& filename,
              const std::vector<int>& col_indices,
              const std::vector<std::string>& dtypes,
              char separator,
              int skip_rows,
              int max_rows,
              std::vector<CsvColumn>& output,
              const CsvLimits& limits = CsvLimits());
#endif /* csv_reader_hpp */
