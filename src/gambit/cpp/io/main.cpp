//
//  main.cpp
//  testcpp
//
//  Created by Sal Abbasi on 9/6/22.
//

#include <iostream>
#include <string>
#include "csv_reader.hpp"

using namespace std;

int main(int argc, char** argv) {
    if (argc != 2) {
        std::cerr << "usage: csv-reader FILE (first column parsed as i8)\n";
        return 2;
    }
    try {
        std::vector<CsvColumn> columns;
        read_csv(argv[1], {0}, {"i8"}, ',', 0, 0, columns);
        std::cout << columns[0].values.size() / columns[0].itemsize << " rows\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
